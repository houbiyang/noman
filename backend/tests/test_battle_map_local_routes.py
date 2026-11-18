"""Pure route-planner helpers; no DB or scenario qualification."""
from fractions import Fraction as F
from unittest import TestCase

from backend.battle_map_local_routes import (COUNT, directional_cells, directional_parents,
                                            points_for, _finite)
from backend.demo_domain import ServiceError


class TriangleAssets:
    manifest={'parts':[dict(part_id='original',source_layer='terrain',action='classify_base_ground')]}
    def __init__(self,dx,dz):self.dx,self.dz=dx,dz
    def _mesh(self,part):
        return [0,0,0,50,50*self.dx,0,0,50*self.dz,50],[0,1,2]


class LocalRouteHelperTests(TestCase):
    def test_heading_masks_use_exact_longitudinal_and_cross_slope(self):
        blocked=[bytearray(COUNT) for _ in range(COUNT)]
        # Full gradient >35%, but both axis-aligned directional grades pass.
        cardinal,diagonal=directional_cells(TriangleAssets(.3,.3),blocked)
        self.assertEqual(cardinal[0][0],0)
        self.assertEqual(diagonal[0][0],1)
        self.assertEqual(blocked[0][0],0)
        cardinal,diagonal=directional_cells(TriangleAssets(.4,0),blocked)
        self.assertEqual(cardinal[0][0],1)
        self.assertEqual(diagonal[0][0],0)

    def test_directional_search_cannot_cut_unknown_diagonal_corner(self):
        grid=[bytearray(b'\1'*COUNT) for _ in range(COUNT)]
        grid[2][2]=grid[3][3]=0
        self.assertEqual(set(directional_parents(grid,grid,(2,2))),{(2,2)})
        grid[2][3]=grid[3][2]=0
        self.assertIn((3,3),directional_parents(grid,grid,(2,2)))

    def test_route_compression_preserves_turns_and_bounds_each_leg(self):
        previous={(1,1):None,(2,1):(1,1),(3,1):(2,1),(3,2):(3,1),(3,3):(3,2),(3,4):(3,3)}
        points=points_for(previous,(3,4),(70,70),(175,225))
        self.assertEqual(points[0],(70,70));self.assertEqual(points[-1],(175,225))
        self.assertTrue(all(max(abs(a[j]-b[j]) for j in (0,1))<=100 for a,b in zip(points,points[1:])))
        self.assertIn((F(175),F(75)),points)

    def test_finite_serializer_never_rounds_repeating_fraction(self):
        self.assertEqual(_finite(F(4374999,150000)),'29.16666')
        self.assertEqual(_finite(F(-1,8)),'-0.125')
        self.assertEqual(_finite(F(12)),'12')
        with self.assertRaises(ServiceError):_finite(F(1,3))
