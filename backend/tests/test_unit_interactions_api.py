"""隔离PostgreSQL验证：真实命令、版本入库、幂等、角色隔离及恢复。"""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from backend import unit_interactions as ui, spatial
from backend.demo_domain import new_unit
from backend.tests import test_service as baseline


class UnitInteractionApiTests(TestCase):
    setUpClass=classmethod(baseline.CurrentFeatureTests.setUpClass.__func__)
    cleanup_database=classmethod(baseline.CurrentFeatureTests.cleanup_database.__func__)
    view=baseline.CurrentFeatureTests.view
    command=baseline.CurrentFeatureTests.command

    def setUp(self):
        # 独立平地，只替换测试几何，不伪称正式地图验收。
        data=dict(width=2000,length=2000,heightColumns=2,heightRows=2,heights=[0]*4,buildings=[],nodes=[],roads=[])
        self.addCleanup(patch.stopall)
        patch('backend.spatial.map_data',return_value=data).start()
        response=self.client.post('/api/v1/games',headers=dict(self.bootstrap,**{'Idempotency-Key':str(uuid4())}),
            json=dict(title='单位交互隔离验证',is_demo=True,spatial_test=True,unit_interactions=True))
        self.assertEqual(response.status_code,201,response.text)
        self.game_id=response.json()['game_id'];self.path='/api/v1/games/'+self.game_id;self.headers={}
        for side in ('host','red','blue'):
            response=self.client.post('/api/v1/bootstrap/games/'+self.game_id+'/sessions',headers=self.bootstrap,json=dict(role=side))
            self.headers[side]={'Authorization':'Bearer '+response.json()['access_token']}
        self.fixture_state()

    def fixture_state(self):
        with self.settings.connect() as connection:
            game=self.service._game(connection,self.game_id,lock=True)
            state=deepcopy(game['state_payload']);state.update(units=[],camps=[],routes=[])
            state['lifecycle']['phase']='paused'
            for side,kind,x in [('red','ground',500),('blue','ground',900),('red','support',550)]:
                unit=new_unit(side,kind,1,spatial.business((x,500)))
                state['units'].append(unit)
            for plan in state['decisions']['plans'].values():plan['status']='adopted'
            ui.sync_units(state)
            identity=uuid4()
            self.service._snapshot(connection,game['id'],game['active_branch_id'],identity,state,
                game['snapshot_revision']+1,game['world_revision']+1,kind='initial')
            connection.execute('UPDATE games SET head_snapshot_id=%s,control_revision=control_revision+1 WHERE id=%s',(identity,game['id']))

    def attack(self):
        red=self.view('red');a=next(u for u in red['units'] if u['unit_type']=='ground')
        b=red['contacts'][0]
        return dict(unit_id=a['unit_id'],life_id=a['life_id'],action='attack',target_id=b['unit_id'],target_life_id=b['life_id'])

    def test_rule_seed_and_resources_persisted(self):
        self.service.seed_versions()
        with self.settings.connect() as connection:
            row=connection.execute('SELECT definition FROM rule_versions WHERE id=%s',(ui.RULE_ID,)).fetchone()
            self.assertEqual(row['definition'],ui.definition())
            state=self.service._game(connection,self.game_id)['state_payload']
            self.assertEqual(state['units'][0]['ammo'],60)
            self.assertEqual(state['units'][2]['stocks'],dict(energy=200,ammo=120,repair=100))
            self.assertEqual(state['unit_interactions']['rule_version_id'],ui.RULE_ID)

    def test_command_retry_exactly_once_and_restore(self):
        self.command('unit_order',self.attack(),'red')
        saved=self.command('create_save',dict(display_name='开火前'))['result']['save_id']
        view=self.view();key=str(uuid4());body=dict(context=view['context'],command_type='advance_step',payload={})
        first=self.command('advance_step',body=body,key=key)
        second=self.command('advance_step',body=body,key=key)
        self.assertTrue(second['replayed'])
        events=self.view('red')['attack_events']
        self.assertEqual(len(events),6)
        self.assertEqual(len({e['event_id'] for e in events}),6)
        self.assertEqual(self.view('red')['attack_events'],events)
        self.assertEqual(events[-1]['ammo_after'],54)
        self.assertEqual(events[-1]['hp_after'],4)
        self.assertNotIn('audience',events[0])
        self.assertNotIn('stocks',events[0]['source'])
        self.assertEqual(self.view('host')['attack_events'],events)
        self.assertEqual(self.view()['unit_interactions']['elapsed_seconds'],60)
        with self.settings.connect() as connection:
            state=self.service._game(connection,self.game_id)['state_payload']
            self.assertEqual(state['units'][0]['ammo'],54)
            self.assertEqual(state['units'][1]['durability'],4)
            self.assertEqual(len(state['unit_interactions']['events']),6)
        self.command('restore_save',dict(save_id=saved))
        restored=self.view('red');self.assertEqual(restored['units'][0]['ammo'],60)
        self.assertEqual(restored['unit_interactions']['elapsed_seconds'],0)
        self.assertEqual(restored['attack_events'],[])

    def test_role_isolation_and_atomic_rejection(self):
        red=self.view('red');payload=self.attack()
        self.assertEqual({u['side'] for u in red['units']},{'red'})
        self.assertTrue(red['contacts'])
        self.assertNotIn('stocks',red['contacts'][0])
        self.command('unit_order',payload,'blue',expected=403)
        payload['target_life_id']='stale'
        self.command('unit_order',payload,'red',expected=409)
        self.assertEqual(self.view()['context']['snapshot_id'],red['context']['snapshot_id'])

    def test_lost_contact_never_returns_current_hidden_state(self):
        with self.settings.connect() as connection:
            game=self.service._game(connection,self.game_id,lock=True);state=deepcopy(game['state_payload'])
            enemy=next(u for u in state['units'] if u['side']=='blue');enemy['position']=spatial.business((1900,1500));enemy['ammo']=3
            identity=uuid4();self.service._snapshot(connection,game['id'],game['active_branch_id'],identity,state,game['snapshot_revision']+1,game['world_revision']+1,kind='initial')
            connection.execute('UPDATE games SET head_snapshot_id=%s,control_revision=control_revision+1 WHERE id=%s',(identity,game['id']))
        red=self.view('red');self.assertFalse(red['contacts'])
        self.assertEqual(red['last_seen_records'][0]['ammo'],60)
        self.assertAlmostEqual(spatial.meters(red['last_seen_records'][0]['position'])[0],900)
