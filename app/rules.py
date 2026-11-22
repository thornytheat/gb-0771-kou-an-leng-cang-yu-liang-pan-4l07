from app import settings
from app.faults import DockFault

# 超温判定规则按归档版本登记并冻结：warm_limit 摄氏阈值（严格大于判超限）、
# temp_max_age 温度读数有效秒、min_door_pulse 门态抖动秒。新增版本时追加登记，旧版本永不改写。
RULES = {
    'ice-stock/1': dict(warm_limit='4', temp_max_age=600, min_door_pulse=30),
}


def current():
    return for_version(settings.VERSION)


def for_version(version):
    if version not in RULES:
        raise DockFault('NO_RULES', f'规则版本 {version} 未登记', 409)
    return dict(RULES[version], version=version)
