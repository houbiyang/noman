"""文档规则纯函数：不以击杀/步数冒充获胜证据。"""
from copy import deepcopy
import unittest
from pydantic import ValidationError
from backend import battle_rules as rules
from backend.contracts import SettleGoalStage
from backend.runtime import ServiceError


def stage_payload(stage, red=4, blue=2):
    return dict(stage=stage, evidence=f"阶段{stage}逐项核对记录", goals=[
        dict(goal_id=goal, red_completed=i < red, blue_completed=i >= 6-blue, evidence=f"{goal}主持人核实")
        for i, goal in enumerate(rules.GOALS)])


class BattleRuleTests(unittest.TestCase):
    def state(self):
        state = dict(manifest={}, lifecycle=dict(phase="paused", simulation_step=0))
        rules.initialize(state)
        return state

    def test_weighted_result_and_milestones(self):
        state = self.state()
        for stage, red, blue in ((1,4,2), (2,2,4), (3,4,2)):
            state['lifecycle']['simulation_step'] = stage*15
            rules.after_advance(state)
            rules.adjudicate(state, 'host', stage_payload(stage,red,blue), {})
        result = rules.evaluate(state['victory'])
        self.assertEqual((result['redPoints'],result['bluePoints'],result['winner']), (20,16,'red'))
        self.assertEqual(len(result['calculations']),3)
        self.assertIn('反超', result['milestones'][1])
        self.assertEqual(state['lifecycle']['phase'], 'finished')

    def test_tie_has_no_invented_tiebreaker(self):
        state = self.state()
        for n in range(1,4):
            state['lifecycle']['simulation_step'] = n*15
            rules.after_advance(state)
            rules.adjudicate(state, 'host', stage_payload(n,3,3), {})
        result = rules.evaluate(state['victory'])
        self.assertEqual((result['redPoints'],result['bluePoints']), (18,18))
        self.assertFalse(result['final'])
        self.assertIsNone(result['winner'])

    def test_missing_or_future_stage_is_not_win(self):
        state = self.state()
        state['victory']['elapsedMinutes'] = 45
        self.assertFalse(rules.evaluate(state['victory'])['final'])
        state['victory']['stages'][0].update(confirmed=True, redGoals=6,evidence='核对')
        state['victory']['elapsedMinutes'] = 14
        self.assertEqual(rules.evaluate(state['victory'])['redPoints'],0)
        for bad in (None, {}, dict(state['victory'],ruleVersion='unknown'),dict(state['victory'],elapsedMinutes=True)):
            self.assertFalse(rules.evaluate(bad)['final'])

    def test_boundary_guards_and_immutable_stage(self):
        state = self.state()
        with self.assertRaises(ServiceError): rules.adjudicate(state,'host',stage_payload(1),{})
        state['lifecycle']['simulation_step']=15
        rules.after_advance(state)
        with self.assertRaises(ServiceError): rules.before_advance(state)
        with self.assertRaises(ServiceError): rules.adjudicate(state,'red',stage_payload(1),{})
        bad = stage_payload(1); bad['goals'][0]['goal_id']=bad['goals'][1]['goal_id']
        with self.assertRaises(ServiceError): rules.adjudicate(state,'host',bad,{})
        rules.adjudicate(state,'host',stage_payload(1),{})
        rules.before_advance(state)
        with self.assertRaises(ServiceError): rules.adjudicate(state,'host',stage_payload(1),{})

    def test_contract_is_strict_and_evidence_required(self):
        for mutation in ('blank','bool','count','number'):
            bad = stage_payload(1)
            if mutation=='blank': bad['goals'][0]['evidence']='  '
            if mutation=='bool': bad['goals'][0]['red_completed']=1
            if mutation=='count': bad['goals'].pop()
            if mutation=='number': bad['stage']=True
            with self.assertRaises(ValidationError): SettleGoalStage.model_validate(bad)

    def test_single_seat_surrender(self):
        state = self.state()
        state['lifecycle']['simulation_step']=4
        with self.assertRaises(ServiceError): rules.surrender(state,'red',{})
        state['lifecycle']['simulation_step']=5; rules.after_advance(state)
        with self.assertRaises(ServiceError): rules.surrender(state,'host',{})
        rules.surrender(state,'blue',{})
        self.assertEqual(rules.evaluate(state['victory'])['winner'],'red')
        self.assertEqual(state['lifecycle']['phase'],'finished')
