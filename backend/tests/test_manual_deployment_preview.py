"""Deployment contracts and local proxy isolation; no user database writes."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch
from pydantic import ValidationError
from backend.manual_deployment_preview import CampPreviewRequest, preview
from backend.runtime import ServiceError
from backend.tests.test_manual_web_launch import ManualWebLaunchTests


class DeploymentRequestTests(TestCase):
    def test_exact_bounds_zero_counts_and_millimetres(self):
        for n in (0, 1, 10):
            value = CampPreviewRequest(side='blue', unit_type='ground', x_mm=10000001, z_mm=4000001, count=n)
            self.assertEqual(value.x_mm, 10000001)
        for change in ({'count': 11}, {'count': -1}, {'x_mm': 1.2}, {'count': True}, {'side': 'host'}, {'surface_id': 'invented'}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                CampPreviewRequest.model_validate(dict(side='blue', unit_type='ground', x_mm=10000000, z_mm=4000000, count=1) | change)

    def test_no_nonlocal_geometry_fallback(self):
        with self.assertRaises(ServiceError) as e:
            preview(None, CampPreviewRequest(side='red', unit_type='support', x_mm=1000000, z_mm=4000000, count=1))
        self.assertEqual(e.exception.code, 'BATTLE_ENGINE_NOT_READY')


class DeploymentProxyTests(TestCase):
    setUp = ManualWebLaunchTests.setUp
    def test_preview_only_authenticated_current_host_and_no_writes(self):
        placement=dict(side='blue',unit_type='ground',x_mm=10000000,z_mm=4000000,count=3)
        body=dict(self.body,placement=placement)
        before={p.name:p.read_bytes() for p in self.local.iterdir() if p.is_file()}
        for token in ('invalid','source-blue'):
            with self.assertRaises(Exception): self.provider.manual_preview(token,body)
        original=self.provider.request
        def request(method,path,data=None,**options):
            if path=='/battle/deployment-preview':
                self.assertEqual(method,'POST'); self.assertTrue(options['bootstrap']); self.assertEqual(data, dict(placement, other_camps=[], other_units=[]))
                return dict(count=3)
            return original(method,path,data,**options)
        self.provider.request=request
        self.assertEqual(self.provider.manual_preview('source-host',body),dict(count=3))
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.local.iterdir() if p.is_file()})
        self.assertEqual(self.counter,0)

    def test_preview_does_not_accept_user_supplied_geometry(self):
        with self.assertRaises(Exception):
            self.provider.manual_preview('source-host',dict(self.body,placement=dict(side='blue',unit_type='ground',x_mm=1,z_mm=1,count=1,surface_id='fabricated')))


class NeighborClearanceTests(TestCase):
    def test_local_service_envelope_may_touch_neighbor_but_entities_must_not(self):
        from types import SimpleNamespace
        from backend.battle_map_surfaces import rectangle
        from backend.battle_map_motion import position_m
        from backend.manual_deployment_preview import check_neighbors, derived_facility
        checker = SimpleNamespace(allow_derived_facilities=True, camp=lambda c: rectangle(*position_m(c), 30),
            _facility=lambda c: derived_facility(c, None), unit=lambda u: rectangle(*position_m(u), 4))
        def camp(kind, x, z, ax, az):
            return dict(camp_id=kind, unit_type=kind, side='blue', surface_id='test', service_surface_id='test',
                position_mm=dict(x_mm=x,z_mm=z,height_mm=0), service_anchor_mm=dict(x_mm=ax,z_mm=az,height_mm=0))
        recon = camp('recon',10242252,8212270,10242252,8262270)
        ground = camp('ground',10243929,8128589,10243929,8178589)
        # The generated 4m access envelope touches the neighboring camp by 319mm.
        check_neighbors(checker, ground, [], [recon], [])
        check_neighbors(checker, recon, [], [ground], [])
        # The formal access rule is still enforced outside local manual mode.
        checker.allow_derived_facilities = False
        with self.assertRaises(ServiceError) as caught:
            check_neighbors(checker, ground, [], [recon], [])
        self.assertEqual(caught.exception.code, 'BATTLE_CAMP_BLOCKS_ROUTE')
        checker.allow_derived_facilities = True
        overlapping = deepcopy(ground)
        overlapping['position_mm'] = deepcopy(recon['position_mm'])
        with self.assertRaises(ServiceError) as caught:
            check_neighbors(checker, overlapping, [], [recon], [])
        self.assertEqual(caught.exception.code, 'BATTLE_DEPLOYMENT_OVERLAP')
        occupied = dict(unit_type='ground', position_mm=deepcopy(recon['position_mm']))
        with self.assertRaises(ServiceError) as caught:
            check_neighbors(checker, ground, [occupied], [recon], [])
        self.assertEqual(caught.exception.code, 'BATTLE_DEPLOYMENT_OVERLAP')
