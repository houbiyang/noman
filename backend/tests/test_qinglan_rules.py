"""项目计分适配的纯函数与真实HTTP/数据库回归。"""
from copy import deepcopy
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend import battle_rules, qinglan_rules as rules, spatial
from backend.demo_domain import initial_state, TASK_NAMES
from backend.tests import test_battle_reports as reports


def fixture_state(*args):
    state = initial_state(*args)
    points = {p['point_id']: p for p in rules.DEFINITION['objectives']}
    assignments = {('red','ground'):'TASK_A',('blue','ground'):'TASK_C',('blue','support'):'TASK_B'}
    for unit in state['units']:
        key = assignments.get((unit['side'],unit['unit_type']))
        if key:
            point = points[key]
            unit['position'] = spatial.business((float(point['x']),float(point['z'])))
    return state


class QinglanRuleTests(unittest.TestCase):
    def state(self):
        state = fixture_state('s','r',next(iter(TASK_NAMES)))
        state['manifest']['spatial_profile'] = spatial.profile()
        rules.initialize(state)
        state['lifecycle']['phase']='paused'
        return state

    def test_20_40_60_weighted_score_and_immutable_evidence(self):
        state = self.state()
        self.assertEqual(state['lifecycle']['deadline_step'],60)
        for minute in (19,20,21,39,40,59,60):
            state['lifecycle']['simulation_step']=minute
            battle_rules.after_advance(state)
            value=battle_rules.evaluate(battle_rules.evidence(state))
            self.assertEqual(value['final'], minute==60)
            self.assertEqual(value['redPoints'],0 if minute<20 else 1 if minute<40 else 3 if minute<60 else 6)
        self.assertEqual((value['redPoints'],value['bluePoints'],value['winner']),(6,12,'blue'))
        self.assertEqual(state['lifecycle']['phase'],'finished')
        self.assertIn('40′',value['milestones'][1])
        saved=deepcopy(state['victory']['stages'][0])
        state['units']=[]
        self.assertEqual(state['victory']['stages'][0],saved)
        self.assertEqual(battle_rules.evaluate(battle_rules.evidence(state))['bluePoints'],12)

    def test_distance_inclusive_contested_air_dead_and_missing_durability(self):
        state=self.state(); point=rules.DEFINITION['objectives'][0]
        unit=state['units'][1]
        unit['position']=spatial.business((float(point['x'])+400,float(point['z'])))
        self.assertEqual(rules.observe(state)[0]['owner'],'red')
        unit['position']=spatial.business((float(point['x'])+400.001,float(point['z'])))
        self.assertEqual(rules.observe(state)[0]['owner'],'neutral')
        unit['position']=spatial.business((float(point['x']),float(point['z'])))
        blue=next(u for u in state['units'] if u['side']=='blue' and u['unit_type']=='ground')
        blue['position']=deepcopy(unit['position'])
        self.assertEqual(rules.observe(state)[0]['owner'],'contested')
        blue['durability']=0
        self.assertEqual(rules.observe(state)[0]['owner'],'red')
        blue['durability']=100; blue['unit_type']='recon'
        self.assertEqual(rules.observe(state)[0]['owner'],'red')
        unit.pop('durability')
        self.assertEqual(rules.observe(state)[0]['owner'],'neutral')
        self.assertNotIn('unit_id',str(rules.observe(state)))

    def test_preview_golden_scores_and_maximum(self):
        for owners, expected in (
            ((('red','red','blue'), ('red','contested','blue'), ('red','blue','blue')), (7,9,'blue')),
            ((('red','red','blue'), ('red','red','blue'), ('red','blue','blue')), (9,9,None)),
            ((('red','red','red'),)*3, (18,0,'red')),
        ):
            state=self.state(); template=deepcopy(state['units'][1])
            for stage, allocation in enumerate(owners, 1):
                state['units']=[]
                for point, owner in zip(rules.DEFINITION['objectives'], allocation):
                    for side in (('red','blue') if owner=='contested' else (owner,)):
                        unit=deepcopy(template); unit['side']=side
                        unit['position']=spatial.business((float(point['x']),float(point['z'])))
                        state['units'].append(unit)
                state['lifecycle']['simulation_step']=stage*20
                battle_rules.after_advance(state)
                value=battle_rules.evaluate(battle_rules.evidence(state))
                if stage<3: self.assertFalse(value['final'])
                if expected[0]==7 and stage==2:
                    self.assertEqual((value['redPoints'],value['bluePoints']),(4,3))
            self.assertTrue(value['final'])
            self.assertEqual((value['redPoints'],value['bluePoints'],value['winner']),expected)
            self.assertEqual((value['redScore100'], value['blueScore100']), (round(expected[0]*100/18,2), round(expected[1]*100/18,2)))

    def test_score100_uses_rule_maximum_and_preserves_verdict(self):
        state = self.state()
        pending = battle_rules.evaluate(battle_rules.evidence(state))
        self.assertEqual((pending['scoreMaximum'], pending['redScore100'], pending['blueScore100']), (18, 0.0, 0.0))
        for minute in (20, 40, 60):
            state['lifecycle']['simulation_step'] = minute
            battle_rules.after_advance(state)
        result = battle_rules.evaluate(battle_rules.evidence(state))
        self.assertEqual((result['scoreMaximum'], result['redScore100'], result['blueScore100']), (18, 33.33, 66.67))
        self.assertEqual(result['winner'], 'blue')
        invalid = battle_rules.evaluate(None)
        self.assertEqual(invalid['scoreMaximum'], 0)
        legacy = dict(mode='war_goals', ruleVersion=battle_rules.VERSION, elapsedMinutes=45,
            stages=[dict(stage=i+1, confirmed=True, redGoals=r, blueGoals=b, evidence='legacy fixture')
                    for i, (r, b) in enumerate(((4,2),(2,4),(4,2)))])
        result = battle_rules.evaluate(legacy)
        self.assertEqual((result['scoreMaximum'], result['redScore100'], result['blueScore100']), (36, 55.56, 44.44))

    def test_score100_zero_surrender_and_compatibility(self):
        state = self.state()
        state['lifecycle']['simulation_step'] = 5
        value = battle_rules.evidence(state)
        value['elapsedMinutes'] = 5
        value['surrender'] = dict(side='red', minute=5, confirmed=True)
        result = battle_rules.evaluate(value)
        self.assertTrue(result['final'])
        self.assertEqual(result['winner'], 'blue')
        self.assertEqual((result['redScore100'], result['blueScore100']), (0.0, 0.0))
        self.assertEqual((result['redPercent'], result['bluePercent']), (result['redScore100'], result['blueScore100']))
        invalid = battle_rules.evaluate(None)
        self.assertEqual((invalid['scoreMaximum'], invalid['redScore100'], invalid['blueScore100']), (0, 0.0, 0.0))

    def test_freeze_manifest_and_no_manual_stage_overwrite(self):
        state=self.state()
        state['manifest']['victory_rules']['radius_m']=401
        self.assertEqual(rules.DEFINITION['radius_m'],400)
        self.assertFalse(rules.enabled(state))
        state=self.state(); state['lifecycle']['simulation_step']=20
        battle_rules.after_advance(state)
        from backend.runtime import ServiceError
        with self.assertRaises(ServiceError): battle_rules.after_advance(state)

    def test_structured_breakdown_and_pending_totals(self):
        state = self.state()
        for minute in (20, 40):
            state['lifecycle']['simulation_step'] = minute
            battle_rules.after_advance(state)
        result = battle_rules.evaluate(battle_rules.evidence(state))
        self.assertEqual([(s['redPoints'],s['bluePoints'],s['redTotal'],s['blueTotal'],s['settled'])
                          for s in result['stageScores']], [(1,2,1,2,True),(2,4,3,6,True),(0,0,3,6,False)])
        self.assertIn('西岸检查点 · 红1 / 蓝0',result['calculations'][0])
        self.assertIn('红+1 / 蓝+0',result['calculations'][0])

    def test_malformed_timeline_fails_closed_without_partial_score(self):
        state = self.state()
        for minute in (20,40,60):
            state['lifecycle']['simulation_step'] = minute
            battle_rules.after_advance(state)
        for mutation in ('future','gap','overflow','pending_count','pending_points'):
            with self.subTest(mutation=mutation):
                value = deepcopy(state['victory'])
                if mutation == 'future': value['elapsedMinutes'] = 19
                elif mutation == 'overflow': value['elapsedMinutes'] = 61
                else:
                    stage = value['stages'][0 if mutation == 'gap' else 2]
                    stage.update(confirmed=False,redGoals=0,blueGoals=0,objectives=[])
                    if mutation == 'pending_count': stage['redGoals'] = 1
                    if mutation == 'pending_points': stage['objectives'] = deepcopy(state['victory']['stages'][2]['objectives'])
                result = battle_rules.evaluate(value)
                self.assertFalse(result['final'])
                self.assertEqual(result['stageScores'],[])
                self.assertEqual((result['redPoints'],result['bluePoints']),(0,0))

    def test_tie_is_project_draw_and_unknown_points_rejected(self):
        state=self.state(); state['units']=[]
        for minute in (20,40,60):
            state['lifecycle']['simulation_step']=minute; battle_rules.after_advance(state)
        value=battle_rules.evaluate(battle_rules.evidence(state))
        self.assertTrue(value['final']); self.assertEqual(value['verdict'],'平局'); self.assertIsNone(value['winner'])
        for key in ('redGoals','objectives'):
            bad=deepcopy(state['victory'])
            if key=='redGoals': bad['stages'][0][key]=2
            else: bad['stages'][0][key][0]['point_id']='not-on-map'
            self.assertFalse(battle_rules.evaluate(bad)['final'])
        bad=deepcopy(state['victory']); bad['stages'][0]['objectives'][0]['redUnits']=True
        self.assertFalse(battle_rules.evaluate(bad)['final'])


class QinglanReportTests(unittest.TestCase):
    # Reuse helpers, NOT inherited test methods; old-mode suite is run separately.
    setUpClass=reports.BattleReportTests.__dict__['setUpClass']
    cleanup_database=reports.BattleReportTests.__dict__['cleanup_database']
    view=reports.BattleReportTests.view; command=reports.BattleReportTests.command
    approve=reports.BattleReportTests.approve
    advance_to=reports.BattleReportTests.advance_to; archives=reports.BattleReportTests.archives

    def setUp(self):
        with patch('backend.service.initial_state',side_effect=fixture_state):
            response=self.client.post('/api/v1/games',headers=dict(self.bootstrap,**{'Idempotency-Key':str(uuid4())}),json=dict(title='青岚适配规则隔离测试',is_demo=True,spatial_test=True,victory_mode='qinglan_control'))
        self.assertEqual(response.status_code,201,response.text)
        self.game_id=response.json()['game_id']; self.path='/api/v1/games/'+self.game_id
        self.headers={}
        for role in ('host','red','blue'):
            response=self.client.post(f'/api/v1/bootstrap/games/{self.game_id}/sessions',headers=self.bootstrap,json=dict(role=role))
            self.headers[role]={'Authorization':'Bearer '+response.json()['access_token']}

    def started(self):
        for side, x, y in (('red', '10.3', '26.2'), ('blue', '100.3', '50.2')):
            for kind, dx, dy in (('recon', 0, 0), ('ground', 0.6, 0), ('support', 0, 0.5)):
                self.command('place_barracks', dict(side=side, unit_type=kind,
                    position=dict(x=format(float(x)+dx, '.1f'), y=format(float(y)+dy, '.1f'))), side)
        reports.BattleReportTests.started(self)

    def test_full_scoring_idempotent_archive_and_evidence(self):
        self.assertEqual(self.view()['victory']['mode'],'qinglan_control')
        self.started(); self.advance_to(19)
        body=dict(context=self.view()['context'],command_type='advance_step',payload={}); key=str(uuid4())
        first=self.command('advance_step',body=body,key=key)
        self.assertTrue(self.command('advance_step',body=body,key=key)['replayed'])
        self.assertFalse(first['replayed'])
        view=self.view()
        self.assertEqual(view['clock']['phase'],'paused')
        self.assertEqual(view['battle_result']['redPoints'],1)
        self.assertEqual((view['battle_result']['scoreMaximum'], view['battle_result']['redScore100'], view['battle_result']['blueScore100']), (18, 5.56, 11.11))
        self.assertFalse(view['capabilities']['can_settle_goal_stage'])
        self.command('settle_goal_stage',dict(stage=1,evidence='不允许覆盖自动计分',goals=[dict(goal_id=k,red_completed=False,blue_completed=False,evidence='核对') for k in battle_rules.GOALS]),expected=409)
        self.advance_to(60)
        result=self.client.get(self.path+'/result',headers=self.headers['host']).json()
        self.assertEqual(result['result_status'],'settled')
        self.assertEqual((result['result']['redPoints'],result['result']['bluePoints']),(6,12))
        self.assertEqual((result['result']['scoreMaximum'], result['result']['redScore100'], result['result']['blueScore100']), (18, 33.33, 66.67))
        for role in self.headers:
            archive=self.archives(role)['items'][0]
            detail=self.archives(role,'/'+archive['id'])['item']['projection']
            self.assertEqual(detail['battle_result']['bluePoints'],12)
            self.assertEqual((detail['battle_result']['scoreMaximum'], detail['battle_result']['redScore100'], detail['battle_result']['blueScore100']), (18, 33.33, 66.67))
            self.assertEqual(len(detail['victory']['stages'][0]['objectives']),3)
            self.assertNotIn('unit_id',str(detail['victory']))
            self.assertEqual(self.archives(role)['total'],1)

    def test_restore_before_boundary_and_mode_requirement(self):
        response=self.client.post('/api/v1/games',headers=dict(self.bootstrap,**{'Idempotency-Key':str(uuid4())}),json=dict(title='缺少空间测试',is_demo=True,victory_mode='qinglan_control'))
        self.assertEqual(response.status_code,422)
        self.started(); self.advance_to(19)
        save=self.command('create_save',dict(display_name='第一阶段结算前'))['result']['save_id']
        self.advance_to(20)
        self.assertTrue(self.view()['victory']['stages'][0]['confirmed'])
        self.command('restore_save',dict(save_id=save))
        self.assertFalse(self.view()['victory']['stages'][0]['confirmed'])
        self.assertEqual(self.view()['battle_result']['redPoints'],0)
        self.advance_to(20)
        self.assertEqual(self.view()['battle_result']['redPoints'],1)

    def test_project_surrender_and_scheduler_stage_pause(self):
        self.started(); self.advance_to(4)
        self.command('surrender',role='red',expected=409)
        self.advance_to(5)
        self.command('surrender',expected=403)
        self.command('surrender',role='blue')
        result=self.client.get(self.path+'/result',headers=self.headers['red']).json()
        self.assertEqual(result['result']['winner'],'red')
        self.assertTrue(result['result']['final'])
        self.assertEqual((result['result']['redScore100'], result['result']['blueScore100']), (0.0, 0.0))
        self.assertFalse(any(score['settled'] for score in result['result']['stageScores']))
        self.assertIn('投降',result['result']['milestones'][-1])
        for role in self.headers: self.assertEqual(self.archives(role)['total'],1)

    def test_scheduler_halts_after_automatic_stage_score(self):
        self.started(); self.advance_to(19)
        self.command('continue')
        self.service.tick()
        view=self.view()
        self.assertEqual((view['clock']['simulation_step'],view['clock']['phase']),(20,'paused'))
        self.assertTrue(view['battle_result']['stageScores'][0]['settled'])
        self.service.tick()
        self.assertEqual(self.view()['clock']['simulation_step'],20)
        self.command('continue')
        self.service.tick()
        self.assertEqual(self.view()['clock']['simulation_step'],21)
