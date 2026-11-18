"""Bounded-source proof tests; these fixtures are not real-map acceptance."""
from copy import deepcopy
from fractions import Fraction as F
from types import SimpleNamespace
from unittest import TestCase

from backend.battle_map_interaction_build import verify_clear_domains
from backend.battle_map_interactions import ForestIndex, Interactions
from backend.runtime import ServiceError
from backend.tests.test_battle_map_interactions import Terrain, unit


class Assets:
    manifest = {'geometry_sha256': 'fixture'}
    def __init__(self, obstacle=None, role=('settlement', 'Battle_Concrete')):
        self.faces = {'base': [[(F(0), F(20), F(0)), (F(100), F(20), F(0)), (F(0), F(20), F(100))]]}
        self.roles = {'base': ('terrain', 'GroundLow')}
        if obstacle:
            self.faces['obstacle'] = [[tuple(F(v) for v in p) for p in obstacle]]
            self.roles['obstacle'] = role
    def part_ids(self): return tuple(self.faces)
    def annotation(self, p): return dict(zip(('source_layer', 'source_material'), self.roles[p]))
    def face_vertices(self, p, i): return self.faces[p][i]
    def face_count(self, p): return len(self.faces[p])
    def part_bounds3d(self, p):
        vs = sum(self.faces[p], [])
        return tuple(min(v[i] for v in vs) for i in range(3))+tuple(max(v[i] for v in vs) for i in range(3))


def layer():
    return dict(surface_id='real-domain-fixture', ground_surface='open', support_faces={'base': [0]},
                verified_domains=[[[10,10],[30,10],[30,30],[10,30]]])


class LocalInteractionSourceTests(TestCase):
    def test_clear_domain_explicit_not_global_permission(self):
        proof = verify_clear_domains(Assets(), [layer()])
        self.assertEqual(proof['domain_count'], 1)
        self.assertEqual(proof['forest_absence_scope'], 'verified_interaction_domains_only')
        bad = layer(); bad.pop('verified_domains')
        with self.assertRaises(ServiceError): verify_clear_domains(Assets(), [bad])

    def test_vertical_wall_and_boundary_contact_cannot_disappear_on_projection(self):
        for x in (20, 30):
            with self.assertRaises(ServiceError):
                verify_clear_domains(Assets([(x,20,15),(x,40,15),(x,20,25)]), [layer()])

    def test_unknown_forest_blocks_without_guessing_density(self):
        with self.assertRaises(ServiceError):
            verify_clear_domains(Assets([(15,20,15),(25,20,15),(15,20,25)],
                                        ('scrub','ForestFloor')), [layer()])

    def test_known_water_only_extends_air_proof_not_ground_or_unknown_cover(self):
        water = Assets([(15,1,15),(25,1,15),(15,1,25)], ('water', 'Water'))
        with self.assertRaises(ServiceError): verify_clear_domains(water, [layer()])
        verify_clear_domains(water, [layer()], allow_water_overflight=True)
        unknown = Assets([(15,1,15),(25,1,15),(15,1,25)], ('scrub','ForestFloor'))
        with self.assertRaises(ServiceError):
            verify_clear_domains(unknown, [layer()], allow_water_overflight=True)

    def test_remote_building_is_excluded_by_real_extent(self):
        verify_clear_domains(Assets([(50,20,50),(60,20,50),(50,30,60)]), [layer()])

    def test_bridge_underside_is_not_same_collision_layer(self):
        bridge = layer(); bridge['ground_surface'] = 'bridge'
        below = Assets([(15,0,15),(25,20,15),(15,20,25)])
        verify_clear_domains(below, [bridge])
        with self.assertRaises(ServiceError): verify_clear_domains(below, [layer()])
        above = Assets([(15,0,15),(25,F(20001,1000),15),(15,20,25)])
        with self.assertRaises(ServiceError): verify_clear_domains(above, [bridge])

    def test_unverified_ray_is_rejected_before_any_observation_or_fire(self):
        terrain = Terrain(); terrain.ray_domain_clear = lambda a,b: False
        rays = SimpleNamespace(cast=lambda *a: self.fail('unverified ray must never reach mesh test'))
        interactions = Interactions(terrain, rays, ForestIndex([]))
        a, b = unit(kind='ground'), unit('b','red',kind='ground',x=10)
        self.assertFalse(interactions.ray_clear(a,b))
        self.assertFalse(interactions.fire_clear(a,b))
        self.assertEqual(interactions.observe({'units':[a,b]},start_positive={'a','b'},
                                            stable_observers={'a','b'}), {'red':set(),'blue':set()})
