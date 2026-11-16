"""单位交互数值与边界，使用独立平地地图，不依赖本机对局。"""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from backend import unit_interactions as ui, spatial, movement
from backend.demo_domain import initial_state, new_unit
from backend.runtime import ServiceError


class UnitInteractionTests(TestCase):
    def setUp(self):
        self.state=initial_state('test','test','patrol')
        self.state['manifest'].update(spatial_profile=spatial.profile(),map_source_sha256='fixture')
        self.state.update(camps=[],routes=[],units=[])
        self.state['lifecycle']['phase']='paused'
        self.data=dict(width=2000,length=2000,heightColumns=2,heightRows=2,heights=[0]*4,buildings=[],
            nodes=[dict(id='a',x=100,z=500),dict(id='b',x=1900,z=500)],
            roads=[dict(id='road',startId='a',endId='b',width=24,isBridge=False)])
        self.grid=bytearray([8])*160000
        self.meta=dict(source_sha256='fixture',grid_sha256='test',cell_m=5,columns=400,rows=400,
            terrain_names=['unknown','open','field','sparse_forest','dense_forest','water','obstacle','mud','road','bridge','gravel'],no_fly_polygons=[])
        self.addCleanup(patch.stopall)
        patch('backend.movement.navigation',return_value=(self.meta,self.grid)).start()
        patch('backend.spatial.map_data',return_value=self.data).start()
        ui.initialize(self.state)

    def unit(self,side='red',kind='ground',number=1,x=500,z=500):
        u=new_unit(side,kind,number,spatial.business((x,z)))
        self.state['units'].append(u);ui.sync_units(self.state);ui.observe(self.state)
        return u

    def command(self,u,action,target=None,actor=None,**kwargs):
        payload=dict(unit_id=u['unit_id'],life_id=u['life_id'],action=action,resource='energy');payload.update(kwargs)
        if target: payload.update(target_id=target['unit_id'],target_life_id=target['life_id'])
        ui.order(self.state,actor or u['side'],payload)
        return payload

    def test_defaults_and_separate_support_stocks(self):
        a=self.unit(kind='recon');g=self.unit();s=self.unit(kind='support')
        self.assertEqual((a['durability'],g['durability'],s['durability']),(40,100,80))
        self.assertEqual((a['speed_m_per_minute'],g['speed_m_per_minute'],s['speed_m_per_minute']),(800,400,300))
        self.assertEqual(g['ammo'],60)
        self.assertEqual(s['stocks'],dict(energy=200,ammo=120,repair=100))
        self.assertEqual(s['energy'],100)

    def test_attack_cost_cooldown_and_pause(self):
        a=self.unit();b=self.unit('blue',x=900)
        self.command(a,'attack',b)
        ui.tick(self.state);self.assertEqual((a['ammo'],b['durability']),(59,84))
        ui.tick(self.state);self.assertEqual((a['ammo'],b['durability']),(59,84))
        ui.tick(self.state);self.assertEqual((a['ammo'],b['durability']),(58,68))
        self.assertEqual(a['last_fired_at'],15)

    def test_simultaneous_lethal_damage(self):
        a=self.unit();b=self.unit('blue',x=900)
        a['durability']=b['durability']=16
        self.command(a,'attack',b);self.command(b,'attack',a)
        ui.tick(self.state)
        self.assertEqual((a['durability'],b['durability']),(0,0))
        self.assertEqual(len(self.state['unit_interactions']['events']),2)

    def test_ranges_air_ammo_and_lost_visibility(self):
        a=self.unit();b=self.unit('blue',x=1200)
        self.command(a,'attack',b);ui.tick(self.state);self.assertEqual(b['durability'],100)
        b['unit_type']='recon';b['durability']=40;b['docked']=False
        ui.tick(self.state);self.assertEqual(b['durability'],24)
        a['ammo']=0;ui.tick(self.state);self.assertEqual(b['durability'],24)
        b['position']=spatial.business((1800,500));ui.observe(self.state)
        self.assertFalse(self.state['observations']['red']['contacts'])
        seen=self.state['observations']['red']['last_seen_records'][0]
        self.assertAlmostEqual(spatial.meters(seen['position'])[0],1200,5)

    def test_permissions_and_life_instance(self):
        a=self.unit();b=self.unit('blue',x=900)
        with self.assertRaises(ServiceError):self.command(a,'attack',b,actor='blue')
        payload=self.command(a,'attack',b)
        payload['life_id']='old'
        with self.assertRaises(ServiceError):ui.order(self.state,'red',payload)
        payload['life_id']=a['life_id'];payload['target_life_id']='old'
        with self.assertRaises(ServiceError):ui.order(self.state,'red',payload)

    def test_no_friendly_fire_or_wrong_unit_attack(self):
        a=self.unit();b=self.unit(number=2,x=550);s=self.unit(kind='support')
        with self.assertRaises(ServiceError):self.command(a,'attack',b)
        enemy=self.unit('blue',x=900)
        with self.assertRaises(ServiceError):self.command(s,'attack',enemy)

    def test_movement_five_second_budget_and_energy(self):
        a=self.unit();b=self.unit('blue',x=950)
        r=movement.plan(self.state,a,spatial.business((1200,500)),'move')
        self.state['routes']=[r]
        self.command(a,'attack',b);ui.tick(self.state)
        self.assertAlmostEqual(spatial.meters(a['position'])[0],500+400/12,4)
        self.assertAlmostEqual(b['durability'],90.4)
        self.assertAlmostEqual(a['energy'],100-400/12/1000*8*1.25,4)

    def test_moving_no_shot_refunds_surcharge_without_extra_distance(self):
        a=self.unit();b=self.unit('blue',x=950)
        self.state['routes']=[movement.plan(self.state,a,spatial.business((1200,500)),'move')]
        self.command(a,'attack',b)
        for reason in ('empty_ammo','cooldown','out_of_range'):
            with self.subTest(reason=reason):
                a.update(energy=100,ammo=0 if reason=='empty_ammo' else 60,
                         last_fired_at=self.state['unit_interactions']['elapsed_seconds'] if reason=='cooldown' else None)
                if reason=='out_of_range': b['position']=spatial.business((1500,500))
                before=deepcopy(a['position']);ui.tick(self.state)
                moved=ui.distance(dict(position=before),a)
                self.assertAlmostEqual(moved,400/12,4)
                self.assertAlmostEqual(a['energy'],100-moved/1000*8,5)
                self.assertEqual(b['durability'],100)

    def test_support_energy_ammo_repair_conservation(self):
        s=self.unit(kind='support');a=self.unit(x=550);a.update(energy=10,ammo=10,durability=10)
        for action,resource,field in [('resupply','energy','energy'),('resupply','ammo','ammo'),('repair','energy','durability')]:
            stock='repair' if action=='repair' else resource
            before=s['stocks'][stock]
            self.command(s,action,a,resource=resource);ui.tick(self.state)
            self.assertEqual(a[field],12);self.assertEqual(s['stocks'][stock],before-2)

    def test_single_recipient_no_double_service_and_capacity(self):
        a=self.unit(x=550);a['energy']=99
        s=self.unit(kind='support');s2=self.unit(kind='support',number=2,x=590)
        self.command(s,'resupply',a);self.command(s2,'resupply',a)
        ui.tick(self.state)
        self.assertEqual(a['energy'],100)
        self.assertEqual(s['stocks']['energy']+s2['stocks']['energy'],399)

    def test_zero_energy_cannot_act_until_next_tick(self):
        a=self.unit();a['energy']=0;b=self.unit('blue',x=900);s=self.unit(kind='support',x=550)
        with self.assertRaises(ServiceError):self.command(a,'attack',b)
        self.command(s,'resupply',a);ui.tick(self.state)
        self.assertEqual(a['energy'],2)
        self.assertNotIn(a['unit_id'],[c.get('observer_unit_id') for c in self.state['observations']['red']['contacts']])

    def test_death_before_service_no_resurrection(self):
        a=self.unit();a['durability']=16;b=self.unit('blue',x=900);s=self.unit(kind='support',x=550)
        self.command(b,'attack',a);self.command(s,'repair',a)
        ui.tick(self.state)
        self.assertEqual(a['durability'],0)
        self.assertEqual(s['stocks']['repair'],100)

    def test_receiver_cannot_move_or_fire_during_service(self):
        a=self.unit();a['energy']=90;b=self.unit('blue',x=900);s=self.unit(kind='support',x=550)
        self.command(a,'attack',b);self.command(s,'resupply',a)
        self.state['routes']=[movement.plan(self.state,a,spatial.business((1200,500)),'move')]
        ui.tick(self.state)
        self.assertAlmostEqual(spatial.meters(a['position'])[0],500,5);self.assertEqual(a['ammo'],60)
        self.assertEqual(a['energy'],92)

    def test_service_rejects_airborne_out_of_range_self_and_enemy(self):
        s=self.unit(kind='support');a=self.unit(kind='recon',x=550);a['energy']=50
        with self.assertRaises(ServiceError):self.command(s,'resupply',a)
        with self.assertRaises(ServiceError):self.command(s,'resupply',s)
        b=self.unit('blue',x=550)
        with self.assertRaises(ServiceError):self.command(s,'resupply',b)
        a=self.unit(number=2,x=650)
        with self.assertRaises(ServiceError):self.command(s,'resupply',a)

    def test_recon_docking_and_flight_drain(self):
        a=self.unit(kind='recon');ui.tick(self.state)
        self.assertAlmostEqual(a['energy'],100-2/12,5)
        self.state['camps']=[dict(camp_id='c',side='red',position=deepcopy(a['position']),deployment_validated=True)]
        self.command(a,'dock');energy=a['energy'];ui.tick(self.state)
        self.assertEqual(a['energy'],energy)

    def test_barracks_quiet_period_and_stocks(self):
        a=self.unit(kind='support');a.update(energy=1,durability=1,last_damaged_at=0)
        a['stocks']=dict(energy=0,ammo=0,repair=0)
        self.state['camps']=[dict(camp_id='c',side='red',position=deepcopy(a['position']),deployment_validated=True)]
        self.command(a,'barracks');ui.tick(self.state);self.assertEqual(a['energy'],1)
        ui.tick(self.state);self.assertEqual(a['energy'],1)
        ui.tick(self.state);self.assertEqual(a['energy'],51)
        self.assertEqual(a['stocks'],dict(energy=50,ammo=60,repair=50))

    def test_snapshot_rules_and_resource_validation(self):
        a=self.unit();copy=deepcopy(self.state);ui.validate(copy)
        copy['units'][0]['ammo']=61
        with self.assertRaises(ServiceError):ui.validate(copy)
        copy=deepcopy(self.state);copy['unit_interactions']['rules']['attack']['damage']=999
        with self.assertRaises(ServiceError):ui.validate(copy)

    def test_minute_has_twelve_substeps(self):
        a=self.unit();b=self.unit('blue',x=900)
        self.command(a,'attack',b);ui.advance(self.state)
        self.assertEqual(self.state['unit_interactions']['elapsed_seconds'],60)
        self.assertEqual((a['ammo'],b['durability']),(54,4))

    def test_attack_feed_frozen_results_and_stable_ids(self):
        a=self.unit(); b=self.unit('blue',x=900)
        self.command(a,'attack',b); ui.tick(self.state)
        events=ui.attack_events(self.state,'red')
        self.assertEqual(len(events),1)
        event=events[0]
        self.assertEqual((event['hp_after'],event['ammo_after'],event['lethal']),(84,59,False))
        self.assertEqual(event['source']['life_id'],a['life_id'])
        self.assertEqual(ui.attack_events(self.state,'host'),events)
        self.assertNotIn('audience',event)
        self.assertNotIn('stocks',event['target'])
        b['position']=spatial.business((1800,500)); b['durability']=1; ui.observe(self.state)
        self.assertEqual(ui.attack_events(self.state,'red'),events)  # legal history, not live hidden coordinates
        event['source']['position']['x']='mutated'
        self.assertNotEqual(ui.attack_events(self.state,'red')[0],event)

    def test_attack_visibility_frozen_before_simultaneous_death(self):
        a=self.unit(); b=self.unit('blue',x=900)
        a['durability']=b['durability']=16
        self.command(a,'attack',b); self.command(b,'attack',a); ui.tick(self.state)
        for side in ('host','red','blue'):
            events=ui.attack_events(self.state,side)
            self.assertEqual(len(events),2)
            self.assertTrue(all(e['lethal'] and e['hp_after']==0 for e in events))
            self.assertEqual(len({e['event_id'] for e in events}),2)

    def test_hidden_source_event_never_revealed_later(self):
        a=self.unit(); b=self.unit('blue',x=900)
        # Red ground can see its target; blue observation is blocked by a side-specific fixture.
        original=ui.can_see
        with patch('backend.unit_interactions.can_see',side_effect=lambda state,observer,target: observer['side']=='red' and original(state,observer,target)):
            ui.observe(self.state); self.command(a,'attack',b); ui.tick(self.state)
        self.assertEqual(len(ui.attack_events(self.state,'host')),1)
        self.assertEqual(len(ui.attack_events(self.state,'red')),1)
        self.assertEqual(ui.attack_events(self.state,'blue'),[])
        ui.observe(self.state)
        self.assertEqual(ui.attack_events(self.state,'blue'),[])
        # Historical pre-upgrade events lack visibility/identity evidence and remain private.
        self.state['unit_interactions']['events'].append(dict(at=0,action='attack',unit_id=a['unit_id'],target_id=b['unit_id'],amount=16))
        self.assertEqual(len(ui.attack_events(self.state,'host')),1)

    def test_no_animation_event_for_invalid_or_empty_attack(self):
        a=self.unit(); b=self.unit('blue',x=900)
        self.command(a,'attack',b); a['ammo']=0; ui.tick(self.state)
        self.assertEqual(ui.attack_events(self.state,'host'),[])
