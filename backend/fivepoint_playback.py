"""五点规则的观看游标；渲染所有面板时必须读取同一个历史快照。"""

from fractions import Fraction

from backend.fivepoint_parameters import number, require


def playback_rate(authority_second: int, played_second, *, stalled=False, finished=False) -> Fraction:
    """由缓存量决定播放倍率；断粮后补满120秒或终局后才能恢复。"""
    played = number(played_second)
    require(type(authority_second) is int and 0 <= played <= authority_second <= 3600,
            "PLAYBACK_TIME_INVALID", "播放时间不能越过权威时间")
    buffer = authority_second - played
    if not buffer or stalled and buffer < 120 and not finished:
        return Fraction(0)
    return Fraction(2) if buffer >= 120 else Fraction(1) if buffer >= 60 else Fraction(1, 2)


def frame_at(frames: list, played_second, branch_id: str) -> dict:
    """按播放时间选择同分支完整快照，终局未来帧不能提前透出。"""
    played = number(played_second)
    eligible = [frame for frame in frames if frame["branch_id"] == branch_id and frame["second"] <= played]
    require(bool(eligible), "PLAYBACK_FRAME_MISSING", "当前分支缺少已播放时刻的快照")
    return max(eligible, key=lambda frame: frame["second"])


def can_produce(authority_second: int, played_second, finished=False) -> bool:
    """只在共同规划边界且缓存不超过120秒时生产下一轮。"""
    played = number(played_second)
    require(type(authority_second) is int and 0 <= played <= authority_second <= 3600,
            "PLAYBACK_TIME_INVALID", "播放时间无效")
    return not finished and authority_second < 3600 and authority_second % 60 == 0 and authority_second - played <= 120


def start_or_resume(authority_second: int, played_second, *, started=False, stalled=False, finished=False) -> Fraction:
    """统一初始180秒门槛、断流120秒门槛与提前终局排空条件。"""
    rate = playback_rate(authority_second, played_second, stalled=stalled, finished=finished)
    return Fraction(0) if not started and authority_second - number(played_second) < 180 and not finished else rate
