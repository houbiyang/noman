"""战争目标、档案隔离、幂等和恢复的真实 PostgreSQL/HTTP 集成验证。"""
from concurrent.futures import ThreadPoolExecutor
import json
import unittest
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from backend.tests import test_service as legacy
from backend.tests.test_battle_rules import stage_payload
from backend.main import create_app


class BattleReportTests(unittest.TestCase):
    setUpClass = classmethod(legacy.CurrentFeatureTests.setUpClass.__func__)
    cleanup_database = classmethod(legacy.CurrentFeatureTests.cleanup_database.__func__)
    view = legacy.CurrentFeatureTests.view
    command = legacy.CurrentFeatureTests.command
    approve = legacy.CurrentFeatureTests.approve
    started = legacy.CurrentFeatureTests.started

    def setUp(self):
        response=self.client.post('/api/v1/games',headers=dict(self.bootstrap,**{'Idempotency-Key':str(uuid4())}),json=dict(title='规则后端隔离验证',is_demo=True,victory_mode='war_goals'))
        self.assertEqual(response.status_code,201,response.text)
        self.game_id=response.json()['game_id']; self.path='/api/v1/games/'+self.game_id
        self.headers={}
        for role in ('host','red','blue'):
            response=self.client.post(f'/api/v1/bootstrap/games/{self.game_id}/sessions',headers=self.bootstrap,json=dict(role=role))
            self.assertEqual(response.status_code,200,response.text)
            self.headers[role]={'Authorization':'Bearer '+response.json()['access_token']}

    def advance_to(self, minute):
        while self.view()['clock']['simulation_step'] < minute: self.command('advance_step')

    def archives(self,role='host',suffix=''):
        response=self.client.get(self.path+'/archives'+suffix,headers=self.headers[role])
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_full_45_minutes_and_private_immutable_archives(self):
        self.started()
        for n,r,b in ((1,4,2),(2,2,4),(3,4,2)):
            self.advance_to(n*15)
            view=self.view()
            self.assertEqual(view['clock']['phase'],'paused')
            self.assertFalse(view['capabilities']['can_advance'])
            self.command('continue',expected=409)
            self.command('settle_goal_stage',stage_payload(n,r,b))
        response=self.client.get(self.path+'/result',headers=self.headers['blue'])
        self.assertEqual(response.status_code,200,response.text)
        result=response.json()
        self.assertEqual(result['result_status'],'settled')
        self.assertEqual((result['result']['redPoints'],result['result']['bluePoints']),(20,16))
        self.assertEqual(len(result['result']['milestones']),3)
        self.assertFalse(result['victory']['preview'])
        ids=[]
        for role in ('host','red','blue'):
            listed=self.archives(role)
            self.assertEqual(listed['total'],1)
            archive=listed['items'][0]; ids.append(archive['id'])
            self.assertTrue(archive['automatic'])
            detail=self.archives(role,'/'+archive['id'])['item']['projection']
            self.assertTrue(detail['read_only'])
            self.assertNotIn('context',detail)
            self.assertNotIn('decisions',detail)
            self.assertNotIn('controller_session_id',detail['clock'])
            if role!='host': self.assertTrue(all(u['side']==role for u in detail['units']))
            self.assertFalse(any('建议' in e['title'] for e in detail['timeline']))
            for h in self.headers.values(): self.assertNotIn(h['Authorization'][7:],json.dumps(detail))
        response=self.client.get(self.path+'/archives/'+ids[1],headers=self.headers['blue'])
        self.assertEqual(response.status_code,404)
        # 新服务实例从 PostgreSQL 读取原结果和同一份档案。
        with TestClient(create_app(self.settings)) as restarted:
            response=restarted.get(self.path+'/archives',headers=self.headers['red'])
            self.assertEqual(response.json()['items'][0]['id'],ids[1])
        for operation in ('UPDATE match_archives SET title=\'篡改\' WHERE id=%s','DELETE FROM match_archives WHERE id=%s'):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with self.settings.connect() as connection: connection.execute(operation,(ids[0],))

    def test_stage_validation_idempotency_and_concurrency(self):
        self.started()
        self.command('settle_goal_stage',stage_payload(1),expected=409)
        self.advance_to(15)
        self.command('settle_goal_stage',stage_payload(1),role='red',expected=403)
        bad=stage_payload(1); bad['goals'][0]['goal_id']='capture-2'
        self.command('settle_goal_stage',bad,expected=422)
        bad=stage_payload(1); bad['goals'][0]['red_completed']=1
        self.command('settle_goal_stage',bad,expected=422)
        body=dict(context=self.view()['context'],command_type='settle_goal_stage',payload=stage_payload(1))
        key=str(uuid4())
        def submit(_): return self.client.post(self.path+'/commands',headers=dict(self.headers['host'],**{'Idempotency-Key':key}),json=body)
        with ThreadPoolExecutor(max_workers=2) as pool: responses=list(pool.map(submit,range(2)))
        self.assertEqual([r.status_code for r in responses],[200,200])
        self.assertEqual(sum(r.json()['replayed'] for r in responses),1)
        self.command('settle_goal_stage',stage_payload(1),expected=409)
        self.command('settle_goal_stage',stage_payload(2),expected=409)
        stale=self.client.post(self.path+'/commands',headers=dict(self.headers['host'],**{'Idempotency-Key':str(uuid4())}),json=body)
        self.assertEqual(stale.status_code,409)

    def test_manual_archive_paging_filter_and_no_mutation(self):
        self.command('archive_match',dict(display_name='   '),expected=422)
        self.command('archive_match',dict(display_name='名'*81),expected=422)
        before=self.view()['context']
        first=self.command('archive_match',dict(display_name='检查%_档案'))['result']['archive_id']
        self.assertEqual(before,self.view()['context'])
        self.command('archive_match',dict(display_name='另一个记录'))
        self.assertEqual(self.archives(suffix='?search=%25_')['total'],1)
        page=self.archives(suffix='?limit=1&offset=1&phase=active')
        self.assertEqual(page['total'],2); self.assertEqual(len(page['items']),1)
        self.assertEqual(self.archives(suffix='?phase=finished')['total'],0)
        self.assertEqual(self.archives('red')['total'],0)
        self.assertEqual(self.client.get(self.path+'/archives/'+first,headers=self.headers['red']).status_code,404)
        for query in ('?limit=101','?offset=-1','?phase=bogus'):
            self.assertEqual(self.client.get(self.path+'/archives'+query,headers=self.headers['host']).status_code,422)
        self.assertEqual(self.client.get(self.path+'/archives').status_code,401)

    def test_surrender_and_auto_archive_exactly_once(self):
        self.started(); self.advance_to(4)
        self.command('surrender',role='red',expected=409)
        self.advance_to(5)
        self.command('surrender',expected=403)
        body=dict(context=self.view('blue')['context'],command_type='surrender',payload={}); key=str(uuid4())
        self.command('surrender',role='blue',body=body,key=key)
        self.assertTrue(self.command('surrender',role='blue',body=body,key=key)['replayed'])
        result=self.client.get(self.path+'/result',headers=self.headers['red']).json()
        self.assertEqual(result['result']['winner'],'red')
        for role in self.headers: self.assertEqual(self.archives(role)['total'],1)
        self.command('surrender',role='red',expected=409)

    def test_restore_before_stage_keeps_future_evidence_out(self):
        self.started(); self.advance_to(14)
        save=self.command('create_save',dict(display_name='阶段前'))['result']['save_id']
        branch=self.view()['context']['branch_id']
        self.advance_to(15); self.command('settle_goal_stage',stage_payload(1))
        self.command('restore_save',dict(save_id=save))
        view=self.view()
        self.assertNotEqual(view['context']['branch_id'],branch)
        self.assertEqual(view['clock']['simulation_step'],14)
        self.assertFalse(view['victory']['stages'][0]['confirmed'])
        self.assertEqual(view['battle_result']['redPoints'],0)

    def test_scheduler_pauses_at_stage_boundary(self):
        self.started(); self.advance_to(14); self.command('continue')
        self.service.tick()
        self.assertEqual(self.view()['clock']['simulation_step'],15)
        self.assertEqual(self.view()['clock']['phase'],'paused')
        self.service.tick()
        self.assertEqual(self.view()['clock']['simulation_step'],15)

    def test_tie_remains_pending_and_archived(self):
        self.started()
        for n in range(1,4):
            self.advance_to(n*15); self.command('settle_goal_stage',stage_payload(n,3,3))
        result=self.client.get(self.path+'/result',headers=self.headers['host']).json()
        self.assertEqual(result['result_status'],'pending_adjudication')
        self.assertEqual(result['result']['verdict'],'待裁定')
        self.assertEqual(self.archives()['total'],1)

    def test_long_game_title_does_not_break_end_transaction(self):
        # A valid 80-character game title must not make a generated archive name
        # violate its own 80-character constraint and roll back surrender.
        with self.settings.connect() as connection:
            connection.execute('UPDATE games SET title=%s WHERE id=%s', ('长'*80,self.game_id))
        self.started(); self.advance_to(5)
        self.command('surrender',role='red')
        for role in self.headers:
            items=self.archives(role)['items']
            self.assertEqual(len(items),1)
            self.assertEqual(len(items[0]['title']),80)
