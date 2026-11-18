"""Read-only camp placement and fixed-roster spawn preview on verified map surfaces."""
from copy import deepcopy
from fractions import Fraction as F
from typing import Annotated, Literal
from pydantic import Field
from backend.contracts import StrictModel, UnitType
from backend.demo_domain import barracks_id, require
from backend.runtime import ServiceError
from backend.battle_map_deployment import _disc_polygon, _touch
from backend.battle_map_motion import position_m, sweep
from backend.battle_setup import CampDeployment, Deployment
from backend.battle_kernel import profiles
from backend.battle_terrain import rational as q


class CampPreviewRequest(StrictModel):
    side: Literal['blue', 'red']
    unit_type: UnitType
    x_mm: Annotated[int, Field(strict=True, ge=0, le=12000000)]
    z_mm: Annotated[int, Field(strict=True, ge=0, le=12000000)]
    count: Annotated[int, Field(strict=True, ge=0, le=10)]
    other_camps: list[CampDeployment] = Field(default_factory=list, max_length=2)
    other_units: list[Deployment] = Field(default_factory=list, max_length=20)


def derived_facility(camp, catalog):
    """Server-derived same-layer paths; every footprint and air column is checked by callers."""
    anchor = camp['service_anchor_mm']
    point = [F(anchor['x_mm'], 1000), F(anchor['z_mm'], 1000)]
    return dict(camp_id=camp['camp_id'], camp_position_mm=deepcopy(camp['position_mm']),
        service_anchor_mm=deepcopy(anchor), service_surface_id=camp['service_surface_id'],
        surface_id=camp['surface_id'], entrance_mm=deepcopy(anchor),
        dock_surface_id=camp['service_surface_id'], dock_position_mm=deepcopy(anchor),
        paths=[[point, point], [point, point]])


def check_neighbors(deployment, camp, units, other_camps, other_units):
    """Check camp and roster footprints using the same access policy as game creation."""
    camps = [camp] + other_camps
    polygons = [deployment.camp(c) for c in camps]
    require(not any(_touch(polygons[0], p) for p in polygons[1:]),
            'BATTLE_DEPLOYMENT_OVERLAP', '兵营与已部署兵营重叠')
    for c in camps:
        facility = deployment._facility(c)
        if not deployment.allow_derived_facilities:
            for path in facility['paths']:
                points = [tuple(q(v) for v in point) for point in path]
                for a, b in zip(points, points[1:]):
                    require(not any(_touch(sweep(a, b, 4), p) for p in polygons),
                            'BATTLE_CAMP_BLOCKS_ROUTE', '兵营阻挡相邻兵营补给通道，请重新生成该营单位或调整选址')
    for unit in units + other_units:
        require(not any(_touch(deployment.unit(unit), p) for p in polygons),
                'BATTLE_DEPLOYMENT_OVERLAP', '出兵区触及相邻兵营')
    for unit in units:
        for other in other_units:
            radius = profiles()[unit['unit_type']]['ground_radius_m'] + profiles()[other['unit_type']]['ground_radius_m'] + 1
            require(sum((a-b)**2 for a,b in zip(position_m(unit), position_m(other))) > radius**2,
                    'BATTLE_DEPLOYMENT_OVERLAP', '相邻兵营出兵区重叠')


def validate_setup(geometry, request):
    """Same full preparation gate as creation, entirely in memory and without database writes."""
    from backend.battle_map_geometry import LocalManualMapGeometry
    from backend.battle_setup import build
    from backend.battle_execution import Executor
    require(type(geometry) is LocalManualMapGeometry, 'BATTLE_ENGINE_NOT_READY', '本地手动地图执行器尚未就绪', 503)
    state = build(request, geometry.rules, geometry.scenario, geometry.capacity, geometry)
    Executor(geometry).prepare_new_game(state)
    return dict(valid=True)


def preview(geometry, request):
    from backend.battle_map_geometry import LocalManualMapGeometry
    require(type(geometry) is LocalManualMapGeometry, 'BATTLE_ENGINE_NOT_READY', '当前服务未接入本地手动地图执行器', 503)
    geometry._verify_sources()
    catalog, deployment = geometry.catalog, geometry.deployment
    # Only reviewed camp-access layers. No nearest-layer snapping or fabricated surface.
    layers = list(dict.fromkeys(f['surface_id'] for f in catalog.payload.get('camp_facilities', [])))
    other_camps = [dict(camp_id=barracks_id(request.side, c.unit_type), side=request.side,
        unit_type=c.unit_type, position_mm=c.position.model_dump(), surface_id=c.surface_id,
        service_anchor_mm=c.service_anchor.model_dump(), service_surface_id=c.service_surface_id)
        for c in request.other_camps]
    other_units = [dict(side=request.side, unit_type=u.unit_type, position_mm=u.position.model_dump(),
        surface_id=u.surface_id, air_state='docked') for u in request.other_units]
    last_error = None
    for layer in layers:
        try:
            point = dict(x_mm=request.x_mm, z_mm=request.z_mm,
                height_mm=round(catalog.height(layer, (F(request.x_mm,1000), F(request.z_mm,1000)))*1000))
            for dx, dz in ((0, 50000), (0, -50000), (50000, 0), (-50000, 0)):
                try:
                    anchor = dict(x_mm=point['x_mm']+dx, z_mm=point['z_mm']+dz)
                    anchor['height_mm'] = round(catalog.height(layer, (F(anchor['x_mm'],1000),F(anchor['z_mm'],1000)))*1000)
                    camp = dict(camp_id=barracks_id(request.side, request.unit_type), side=request.side,
                        unit_type=request.unit_type, position_mm=point, surface_id=layer,
                        service_anchor_mm=anchor, service_surface_id=layer)
                    polygon = deployment.camp(camp)
                    facility = deployment._facility(camp)
                    require(deployment.dock_airspace_validator(facility) is True, 'BATTLE_DOCK_INVALID', '兵营附近缺少合法停靠空域')
                    require(not any(_disc_polygon(position_m(camp),100,p) for mask in geometry.terrain.capture_masks.values() for p in mask['polygons']),
                        'BATTLE_CAMP_CAPTURE_OVERLAP', '兵营服务范围不能覆盖据点')
                    units = []
                    for ordinal in range(1,request.count+1):
                        # 10m grid outside the camp solid, inside its service radius.
                        along = 45000 + ((ordinal-1)//5)*10000
                        across = ((ordinal-1)%5-2)*10000
                        ux, uz = (across, along if dz > 0 else -along) if dz else (along if dx > 0 else -along, across)
                        pos = dict(x_mm=point['x_mm']+ux, z_mm=point['z_mm']+uz)
                        pos['height_mm'] = round(catalog.height(layer,(F(pos['x_mm'],1000),F(pos['z_mm'],1000)))*1000)
                        unit = dict(unit_type=request.unit_type, side=request.side, position_mm=pos, surface_id=layer, air_state='docked')
                        footprint = deployment.unit(unit)
                        require(not _touch(polygon,footprint), 'BATTLE_DEPLOYMENT_OVERLAP', '出兵区与兵营实体重叠')
                        if request.unit_type == 'recon':
                            require(geometry.terrain.air_legal(unit), 'BATTLE_AIRSPACE_INVALID', '无人机出兵区上方空域不可用')
                        units.append(dict(unit_type=request.unit_type, ordinal=ordinal, position=pos, surface_id=layer))
                    check_neighbors(deployment, camp,
                        [dict(side=request.side, unit_type=u['unit_type'], position_mm=u['position'],
                              surface_id=u['surface_id'], air_state='docked') for u in units], other_camps, other_units)
                    return dict(camp=dict(unit_type=request.unit_type, position=point, surface_id=layer,
                        service_anchor=anchor, service_surface_id=layer), units=units, count=request.count,
                        message='位置通过当前地图静态验收；开战前仍核对双方整体部署。')
                except ServiceError as error:
                    last_error = error
        except ServiceError as error:
            last_error = error
    raise ServiceError(422, 'BATTLE_DEPLOYMENT_INVALID', '该位置无法容纳兵营、服务区及所选编制，请在本方已核验部署区域重新选址。') from last_error
