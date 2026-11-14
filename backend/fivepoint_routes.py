"""已核验分段路线的五秒时间预算积分；几何合法性由地图适配器提供。"""

from fractions import Fraction

from backend.fivepoint_parameters import number, require, segment_cost


def integrate(kind: str, segments: list, seconds=5, energy=100, reserve_factor=1) -> dict:
    """沿完整路线按分段地表和坡度消耗同一时间预算，返回连续参考推进量。

    Args:
        kind: 三类单位之一。
        segments: 已验证的有序线段，每段必须包含长度和地表，可含纵横坡。
        seconds: 本次剩余仿真秒数，不能因路面切换而重置。
        energy: 本步可用能源，移动开火预留通过倍率单独传入。
        reserve_factor: 普通移动为1，移动攻击为5/4。

    Returns:
        精确路程、耗时、基础能耗、预留能源以及每段实际行走长度；不以预留值记实际消耗。
    """
    budget, available, reserve = number(seconds), number(energy), number(reserve_factor)
    require(budget >= 0 and available >= 0 and reserve >= 1, "ROUTE_BUDGET_INVALID", "路线预算无效")
    lengths, elapsed, consumed, total = [], Fraction(0), Fraction(0), Fraction(0)
    for segment in segments:
        length = number(segment["length"])
        cost = segment_cost(kind, segment["terrain"], length, segment.get("longitudinal", 0), segment.get("cross", 0))
        if length == 0:
            lengths.append(Fraction(0))
            continue
        ratio = min(Fraction(1), (budget - elapsed) / cost["seconds"],
                    (available - consumed * reserve) / (cost["energy"] * reserve))
        actual = length * ratio
        lengths.append(actual)
        total += actual
        elapsed += cost["seconds"] * ratio
        consumed += cost["energy"] * ratio
        if ratio < 1 or elapsed == budget or consumed * reserve == available:
            break
    return dict(distance=total, seconds=elapsed, energy=consumed, reserved_energy=consumed * reserve, segments=lengths)
