"""Проверка совместимости комплектующих. Чистая логика, без обращений к БД.

Принципы:
* Совместимость никогда не подтверждается, если для проверки не хватает характеристик —
  такая проверка даёт статус ⚪ «недостаточно данных».
* Итоговый статус: 🔴 > ⚪ > 🟡 > 🟢. Любая найденная несовместимость важнее остального;
  при отсутствии данных «всё хорошо» не показывается.
* Сообщения написаны простым языком: что не так и что с этим делать.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum

from bot.services.parts import CATEGORY_SHORT, CATEGORIES, Part, cpu_max_power


class Severity(IntEnum):
    INFO = 0       # справочная заметка, статус не меняет
    OK = 1
    WARN = 2
    UNKNOWN = 3
    BAD = 4


STATUS_EMOJI = {
    Severity.OK: "🟢",
    Severity.WARN: "🟡",
    Severity.BAD: "🔴",
    Severity.UNKNOWN: "⚪",
    Severity.INFO: "ℹ️",
}

STATUS_TITLE = {
    Severity.OK: "Совместимо",
    Severity.WARN: "Есть ограничения",
    Severity.BAD: "Несовместимо",
    Severity.UNKNOWN: "Недостаточно данных",
}

# Базовое потребление «остальной системы»: плата, память, накопители, вентиляторы
SYSTEM_BASE_LOAD_W = 100
PSU_HEADROOM = 1.25

# Какие чипсеты вообще подходят сокету
SOCKET_CHIPSETS: dict[str, set[str]] = {
    "AM4": {"A520", "B450", "B550", "X470", "X570"},
    "AM5": {"A620", "B650", "B650E", "X670", "X670E", "B840", "B850", "X870", "X870E"},
    "LGA1700": {"H610", "B660", "H670", "Z690", "B760", "H770", "Z790"},
    "LGA1851": {"H810", "B860", "Z890"},
}

# Поддержка поколений процессоров чипсетами: (без обновления BIOS, нужно обновить BIOS)
CHIPSET_FAMILIES: dict[str, tuple[set[str], set[str]]] = {
    "A520": ({"Zen 3"}, set()),
    "B550": ({"Zen 3"}, set()),
    "X570": ({"Zen 3"}, set()),
    "B450": ({"Zen 2"}, {"Zen 3"}),
    "X470": ({"Zen 2"}, {"Zen 3"}),
    "A620": ({"Zen 4"}, {"Zen 5"}),
    "B650": ({"Zen 4"}, {"Zen 5"}),
    "B650E": ({"Zen 4"}, {"Zen 5"}),
    "X670": ({"Zen 4"}, {"Zen 5"}),
    "X670E": ({"Zen 4"}, {"Zen 5"}),
    "B840": ({"Zen 5"}, {"Zen 4"}),
    "B850": ({"Zen 4", "Zen 5"}, set()),
    "X870": ({"Zen 4", "Zen 5"}, set()),
    "X870E": ({"Zen 4", "Zen 5"}, set()),
    "H610": ({"Alder Lake"}, {"Raptor Lake", "Raptor Lake Refresh"}),
    "B660": ({"Alder Lake"}, {"Raptor Lake", "Raptor Lake Refresh"}),
    "H670": ({"Alder Lake"}, {"Raptor Lake", "Raptor Lake Refresh"}),
    "Z690": ({"Alder Lake"}, {"Raptor Lake", "Raptor Lake Refresh"}),
    "B760": ({"Alder Lake", "Raptor Lake", "Raptor Lake Refresh"}, set()),
    "H770": ({"Alder Lake", "Raptor Lake", "Raptor Lake Refresh"}, set()),
    "Z790": ({"Alder Lake", "Raptor Lake", "Raptor Lake Refresh"}, set()),
    "H810": ({"Arrow Lake"}, set()),
    "B860": ({"Arrow Lake"}, set()),
    "Z890": ({"Arrow Lake"}, set()),
}

# Форм-факторы плат, помещающиеся в корпус данного размера (плата меньше корпуса — всегда ок)
_BOARD_FITS_IN: dict[str, set[str]] = {
    "Mini-ITX": {"Mini-ITX", "mATX", "ATX", "E-ATX"},
    "mATX": {"mATX", "ATX", "E-ATX"},
    "ATX": {"ATX", "E-ATX"},
    "E-ATX": {"E-ATX"},
}


@dataclass(frozen=True, slots=True)
class Issue:
    severity: Severity
    code: str
    text: str
    parts: tuple[str, ...] = ()


@dataclass(slots=True)
class CompatReport:
    issues: list[Issue] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    checked_pairs: int = 0
    estimated_load_w: int | None = None

    @property
    def status(self) -> Severity:
        if self.checked_pairs == 0:
            return Severity.UNKNOWN
        worst = max((i.severity for i in self.issues), default=Severity.OK)
        if worst == Severity.INFO:
            return Severity.OK
        return worst

    @property
    def emoji(self) -> str:
        return STATUS_EMOJI[self.status]

    @property
    def title(self) -> str:
        return STATUS_TITLE[self.status]

    def by_severity(self, severity: Severity) -> list[Issue]:
        return [i for i in self.issues if i.severity == severity]

    @property
    def has_blockers(self) -> bool:
        return self.status == Severity.BAD


def _name(part: Part) -> str:
    return part.name


def _add(issues: list[Issue], severity: Severity, code: str, text: str, *parts: Part) -> None:
    issues.append(Issue(severity, code, text, tuple(p.name for p in parts)))


def estimate_load_w(cpu: Part | None, gpu: Part | None) -> int | None:
    """Оценка пикового потребления; None, если данных процессора или видеокарты нет."""
    if cpu is None or gpu is None:
        return None
    cpu_w = cpu_max_power(cpu)
    gpu_w = gpu.spec("tgp_w")
    if cpu_w is None or gpu_w is None:
        return None
    return int(cpu_w + gpu_w + SYSTEM_BASE_LOAD_W)


# ---------------------------------------------------------------- отдельные проверки

def _check_socket(cpu: Part, mb: Part, out: list[Issue]) -> None:
    c_sock, m_sock = cpu.spec("socket"), mb.spec("socket")
    if c_sock is None or m_sock is None:
        _add(out, Severity.UNKNOWN, "socket_unknown",
             "Не удалось сравнить сокеты: у процессора или платы не указан сокет.", cpu, mb)
    elif c_sock != m_sock:
        _add(out, Severity.BAD, "socket",
             f"Процессор ({c_sock}) не подходит к материнской плате ({m_sock}): "
             f"разъёмы разные, процессор физически не встанет. Нужна плата или процессор с сокетом одного типа.",
             cpu, mb)
    else:
        _add(out, Severity.OK, "socket", f"Сокет {c_sock} совпадает у процессора и платы.", cpu, mb)


def _check_chipset_bios(cpu: Part, mb: Part, out: list[Issue]) -> None:
    if cpu.spec("socket") != mb.spec("socket") or cpu.spec("socket") is None:
        return  # причина уже описана проверкой сокета
    chipset, family = mb.spec("chipset"), cpu.spec("family")
    if chipset is None or family is None:
        _add(out, Severity.UNKNOWN, "chipset_unknown",
             "Нет данных о чипсете платы или поколении процессора — поддержку не проверить. "
             "Откройте список совместимых CPU на сайте производителя платы.", cpu, mb)
        return
    allowed = SOCKET_CHIPSETS.get(cpu.spec("socket"))
    if allowed is not None and chipset not in allowed:
        if chipset in set().union(*SOCKET_CHIPSETS.values()):
            _add(out, Severity.BAD, "chipset",
                 f"Чипсет {chipset} не предназначен для сокета {cpu.spec('socket')}.", cpu, mb)
        else:
            _add(out, Severity.UNKNOWN, "chipset_unknown",
                 f"Чипсет {chipset} не найден в справочнике — поддержку процессора не проверить. "
                 "Откройте список совместимых CPU на сайте производителя платы.", cpu, mb)
        return
    known = CHIPSET_FAMILIES.get(chipset)
    if known is None:
        _add(out, Severity.UNKNOWN, "chipset_unknown",
             f"Для чипсета {chipset} нет данных о поддерживаемых поколениях процессоров. "
             "Проверьте список совместимых CPU на сайте производителя платы.", cpu, mb)
        return
    ok_now, needs_update = known
    if family in ok_now:
        _add(out, Severity.OK, "chipset", f"Чипсет {chipset} поддерживает процессор ({family}).", cpu, mb)
    elif family in needs_update:
        _add(out, Severity.WARN, "bios",
             f"Плата на чипсете {chipset} поддерживает этот процессор ({family}) только после обновления BIOS. "
             "Если в магазине стоит старая прошивка, ПК может не запуститься, пока BIOS не обновят "
             "(в магазине можно попросить прошить плату или выбрать плату с пометкой о поддержке).", cpu, mb)
    else:
        _add(out, Severity.BAD, "chipset",
             f"Чипсет {chipset} не поддерживает процессоры поколения {family}.", cpu, mb)


def _check_memory(cpu: Part | None, mb: Part | None, ram: Part | None, out: list[Issue]) -> int:
    if ram is None:
        return 0
    checked = 0
    r_type = ram.spec("type")
    if mb is not None:
        checked += 1
        m_type = mb.spec("memory_type")
        if r_type is None or m_type is None:
            _add(out, Severity.UNKNOWN, "ram_type_unknown",
                 "Не указан тип памяти у платы или модулей — совместимость памяти не проверить.", ram, mb)
        elif r_type != m_type:
            _add(out, Severity.BAD, "ram_type",
                 f"Плата работает с {m_type}, а выбрана память {r_type}. Эти типы механически и электрически "
                 "несовместимы: модуль просто не встанет в слот. Нужна память того же типа, что и у платы.", ram, mb)
        else:
            _add(out, Severity.OK, "ram_type", f"Тип памяти {r_type} подходит плате.", ram, mb)

        slots, modules = mb.spec("ram_slots"), ram.spec("modules")
        if slots is not None and modules is not None:
            if modules > slots:
                _add(out, Severity.BAD, "ram_slots",
                     f"В комплекте {modules} модуля, а на плате только {slots} слота памяти.", ram, mb)
            elif modules == slots and slots > 2:
                _add(out, Severity.INFO, "ram_slots_full",
                     "Все слоты памяти будут заняты — для увеличения объёма позже модули придётся менять.", ram, mb)
        else:
            _add(out, Severity.UNKNOWN, "ram_slots_unknown",
                 "Нет данных о числе слотов памяти или модулей в комплекте.", ram, mb)

    if cpu is not None:
        checked += 1
        types = cpu.spec("mem_types")
        if types is not None and r_type is not None and r_type not in types:
            _add(out, Severity.BAD, "cpu_ram_type",
                 f"Процессор не работает с памятью {r_type} (поддерживает: {', '.join(types)}).", cpu, ram)
        elif types is None or r_type is None:
            _add(out, Severity.UNKNOWN, "cpu_ram_type_unknown",
                 "Нет данных о типах памяти процессора или модулей.", cpu, ram)
        limits = cpu.spec("max_mem_mhz")
        speed = ram.spec("speed_mhz")
        if isinstance(limits, Mapping) and speed is not None and r_type in limits and speed > limits[r_type]:
            _add(out, Severity.INFO, "ram_speed",
                 f"Память рассчитана на {speed} МГц, а официальный предел процессора — {limits[r_type]} МГц. "
                 "Чаще всего работает через профиль XMP/EXPO, но формально это разгон: "
                 "производитель процессора частоту выше предела не гарантирует.", cpu, ram)

    # Объём
    capacity = ram.spec("capacity_gb")
    for owner, label in ((mb, "платы"), (cpu, "процессора")):
        if owner is None:
            continue
        limit = owner.spec("max_ram_gb")
        if capacity is None:
            _add(out, Severity.UNKNOWN, "ram_capacity_unknown", "Не указан объём памяти.", ram)
            break
        if limit is None:
            _add(out, Severity.UNKNOWN, "ram_limit_unknown",
                 f"Нет данных о максимальном объёме памяти {label}.", owner, ram)
        elif capacity > limit:
            _add(out, Severity.BAD, "ram_capacity",
                 f"Объём памяти {capacity} ГБ больше максимума {label}: {limit} ГБ.", owner, ram)
    return checked


def _check_case_board(mb: Part, case: Part, out: list[Issue]) -> None:
    b_form, c_forms = mb.spec("form_factor"), case.spec("form_factors")
    if b_form is None or c_forms is None:
        _add(out, Severity.UNKNOWN, "form_unknown",
             "Не указан форм-фактор платы или корпуса — установить плату в корпус не проверить.", mb, case)
        return
    fits = _BOARD_FITS_IN.get(b_form)
    if fits is None:
        _add(out, Severity.UNKNOWN, "form_unknown", f"Неизвестный форм-фактор платы: {b_form}.", mb, case)
    elif b_form in c_forms:
        _add(out, Severity.OK, "form", f"Плата {b_form} подходит корпусу.", mb, case)
    else:
        _add(out, Severity.BAD, "form",
             f"Плата формата {b_form} не поддерживается корпусом (он рассчитан на {', '.join(c_forms)}). "
             "Нужен корпус большего размера либо плата меньшего формата.", mb, case)


def _check_gpu_case(gpu: Part, case: Part, out: list[Issue]) -> None:
    limit, length = case.spec("max_gpu_length_mm"), gpu.spec("length_mm")
    if limit is None:
        _add(out, Severity.UNKNOWN, "gpu_length_unknown",
             "У корпуса не указана максимальная длина видеокарты.", gpu, case)
    elif length is None:
        _add(out, Severity.WARN, "gpu_length_model",
             f"Длина видеокарты зависит от конкретной модели производителя. Корпус вмещает карты до {limit} мм — "
             "перед покупкой сверьте длину выбранной модели.", gpu, case)
    elif length > limit:
        _add(out, Severity.BAD, "gpu_length",
             f"Видеокарта ({length} мм) длиннее, чем допускает корпус ({limit} мм).", gpu, case)
    elif limit - length < 10:
        _add(out, Severity.WARN, "gpu_length_tight",
             f"Видеокарта ({length} мм) встаёт в корпус ({limit} мм) почти вплотную — "
             "это затруднит установку и воздушный поток.", gpu, case)
    else:
        _add(out, Severity.OK, "gpu_length", f"Видеокарта ({length} мм) помещается в корпус ({limit} мм).", gpu, case)


def _check_cooler(cpu: Part | None, cooler: Part, case: Part | None, out: list[Issue]) -> int:
    checked = 0
    if cpu is not None:
        checked += 1
        sockets, c_sock = cooler.spec("sockets"), cpu.spec("socket")
        if sockets is None or c_sock is None:
            _add(out, Severity.UNKNOWN, "cooler_socket_unknown",
                 "Нет данных о сокетах кулера или процессора — крепление не проверить.", cooler, cpu)
        elif c_sock not in sockets:
            _add(out, Severity.BAD, "cooler_socket",
                 f"Крепление кулера не рассчитано на сокет {c_sock} (поддерживает: {', '.join(sockets)}).", cooler, cpu)
        else:
            _add(out, Severity.OK, "cooler_socket", f"Кулер крепится на сокет {c_sock}.", cooler, cpu)

        c_tdp, power = cooler.spec("tdp_w"), cpu_max_power(cpu)
        base_tdp = cpu.spec("tdp_w")
        if c_tdp is None or power is None:
            _add(out, Severity.UNKNOWN, "cooler_tdp_unknown",
                 "Нет данных о теплоотводе кулера или тепловыделении процессора.", cooler, cpu)
        elif base_tdp is not None and c_tdp < base_tdp:
            _add(out, Severity.BAD, "cooler_tdp",
                 f"Кулер рассчитан на {c_tdp} Вт, а процессор выделяет минимум {base_tdp} Вт — охлаждения не хватит.",
                 cooler, cpu)
        elif c_tdp < power:
            _add(out, Severity.WARN, "cooler_tdp_peak",
                 f"Кулер рассчитан на {c_tdp} Вт, а в пике процессор потребляет до {power} Вт: "
                 "под высокой нагрузкой возможен перегрев и снижение частот.", cooler, cpu)
        else:
            _add(out, Severity.OK, "cooler_tdp", f"Кулер ({c_tdp} Вт) справится с тепловыделением процессора.", cooler, cpu)
    if case is not None:
        checked += 1
        height, limit = cooler.spec("height_mm"), case.spec("max_cooler_height_mm")
        if height is None or limit is None:
            _add(out, Severity.UNKNOWN, "cooler_height_unknown",
                 "Нет данных о высоте кулера или допустимой высоте в корпусе.", cooler, case)
        elif height > limit:
            _add(out, Severity.BAD, "cooler_height",
                 f"Кулер высотой {height} мм не поместится в корпус (максимум {limit} мм): боковая крышка не закроется.",
                 cooler, case)
        elif limit - height < 5:
            _add(out, Severity.WARN, "cooler_height_tight",
                 f"Кулер ({height} мм) почти упирается в крышку корпуса (лимит {limit} мм).", cooler, case)
        else:
            _add(out, Severity.OK, "cooler_height", f"Кулер ({height} мм) помещается в корпус (лимит {limit} мм).", cooler, case)
    return checked


def _check_psu(cpu: Part | None, gpu: Part | None, psu: Part, out: list[Issue]) -> int:
    if cpu is None or gpu is None:
        return 0
    wattage = psu.spec("wattage")
    load = estimate_load_w(cpu, gpu)
    if wattage is None or load is None:
        _add(out, Severity.UNKNOWN, "psu_unknown",
             "Недостаточно данных для расчёта нагрузки на блок питания "
             "(нужны мощность БП, потребление процессора и видеокарты).", psu)
        return 1

    rec = gpu.spec("rec_psu_w")
    if wattage < load:
        _add(out, Severity.BAD, "psu_power",
             f"Расчётная пиковая нагрузка около {load} Вт, а блок питания даёт {wattage} Вт — "
             "его не хватит, ПК будет выключаться под нагрузкой.", psu, gpu, cpu)
    elif rec is not None and wattage < rec:
        _add(out, Severity.WARN, "psu_recommended",
             f"Блок питания ({wattage} Вт) слабее рекомендации производителя видеокарты ({rec} Вт). "
             f"Расчётная нагрузка {load} Вт, так что запуск возможен, но запас небольшой.", psu, gpu)
    elif wattage < load * PSU_HEADROOM:
        _add(out, Severity.WARN, "psu_headroom",
             f"Блок питания ({wattage} Вт) потянет расчётную нагрузку ({load} Вт), но запаса менее 25% — "
             "при апгрейде или с возрастом БП может не хватить.", psu, gpu)
    else:
        _add(out, Severity.OK, "psu_power",
             f"Мощности блока питания ({wattage} Вт) достаточно: расчётная нагрузка около {load} Вт.", psu, gpu)

    # Разъёмы
    connectors = gpu.spec("power_connectors")
    if connectors is None:
        _add(out, Severity.WARN, "psu_connectors_model",
             "Разъёмы питания видеокарты зависят от конкретной модели (6/8-pin или 12V-2x6). "
             "Перед покупкой сверьте их с кабелями блока питания.", gpu, psu)
    elif "12V-2x6" in connectors or "12VHPWR" in connectors:
        has = psu.spec("has_12v_2x6")
        if has is None:
            _add(out, Severity.UNKNOWN, "psu_12v_unknown",
                 "Нет данных, есть ли у блока питания разъём 12V-2x6.", psu, gpu)
        elif not has:
            _add(out, Severity.WARN, "psu_12v",
                 "Видеокарте нужен разъём 12V-2x6, а у блока питания его нет — понадобится переходник "
                 "(он работает, но менее надёжен, чем родной кабель).", psu, gpu)
        else:
            _add(out, Severity.OK, "psu_12v", "У блока питания есть разъём 12V-2x6 для видеокарты.", psu, gpu)
    return 1


def _check_storage(mb: Part | None, ssd: Part, ssd_count_nvme: int, out: list[Issue]) -> int:
    if mb is None:
        return 0
    iface = ssd.spec("interface")
    if iface is None:
        _add(out, Severity.UNKNOWN, "ssd_unknown", "Не указан интерфейс накопителя.", ssd, mb)
        return 1
    if iface == "NVMe":
        slots = mb.spec("m2_slots")
        if slots is None:
            _add(out, Severity.UNKNOWN, "m2_unknown", "Нет данных о числе слотов M.2 на плате.", ssd, mb)
        elif slots < 1 or ssd_count_nvme > slots:
            _add(out, Severity.BAD, "m2",
                 f"Накопителей M.2 NVMe больше ({ssd_count_nvme}), чем слотов на плате ({slots}).", ssd, mb)
        else:
            _add(out, Severity.OK, "m2", f"На плате есть слот M.2 для NVMe-накопителя (всего слотов: {slots}).", ssd, mb)
    elif iface == "SATA":
        ports = mb.spec("sata_ports")
        if ports is None:
            _add(out, Severity.UNKNOWN, "sata_unknown", "Нет данных о числе портов SATA на плате.", ssd, mb)
        elif ports < 1:
            _add(out, Severity.BAD, "sata", "На плате нет свободных портов SATA.", ssd, mb)
        else:
            _add(out, Severity.OK, "sata", "На плате есть порт SATA для накопителя.", ssd, mb)
    else:
        _add(out, Severity.UNKNOWN, "ssd_unknown", f"Неизвестный интерфейс накопителя: {iface}.", ssd, mb)
    return 1


def _check_pcie(cpu: Part | None, mb: Part | None, gpu: Part, out: list[Issue]) -> int:
    if mb is None:
        return 0
    g_gen, g_lanes = gpu.spec("pcie_gen"), gpu.spec("pcie_lanes")
    gens = [g for g in (mb.spec("pcie_x16_gen"), cpu.spec("pcie_gen") if cpu else None) if g is not None]
    if g_gen is None or not gens:
        _add(out, Severity.UNKNOWN, "pcie_unknown",
             "Нет данных о версии PCIe у видеокарты, платы или процессора.", gpu, mb)
        return 1
    effective = min(gens)
    if effective >= g_gen:
        _add(out, Severity.OK, "pcie", f"Видеокарта работает на полной скорости PCIe {g_gen}.0.", gpu, mb)
    elif g_lanes is not None and g_lanes <= 8 and effective < 4:
        _add(out, Severity.WARN, "pcie_limit",
             f"Видеокарта подключается по PCIe {g_gen}.0 x{g_lanes}, а плата или процессор дают только "
             f"PCIe {effective}.0. На такой шине у карт с 8 линиями потери производительности могут быть заметными.", gpu, mb)
    else:
        _add(out, Severity.INFO, "pcie_limit",
             f"Видеокарта (PCIe {g_gen}.0) будет работать на PCIe {effective}.0: "
             "работать будет, потери обычно небольшие.", gpu, mb)
    return 1


# ---------------------------------------------------------------- публичный интерфейс

def check_compatibility(parts: Mapping[str, Part | None]) -> CompatReport:
    """Проверяет набор комплектующих {категория: деталь}. Частичные наборы допустимы."""
    cpu, gpu, mb = parts.get("cpu"), parts.get("gpu"), parts.get("motherboard")
    ram, ssd, psu = parts.get("ram"), parts.get("ssd"), parts.get("psu")
    case, cooler = parts.get("case"), parts.get("cooler")

    report = CompatReport()
    report.missing = [CATEGORY_SHORT[c] for c in CATEGORIES if parts.get(c) is None]
    out = report.issues
    pairs = 0

    if cpu is not None and mb is not None:
        pairs += 1
        _check_socket(cpu, mb, out)
        _check_chipset_bios(cpu, mb, out)
    if ram is not None and (mb is not None or cpu is not None):
        pairs += _check_memory(cpu, mb, ram, out)
    if mb is not None and case is not None:
        pairs += 1
        _check_case_board(mb, case, out)
    if gpu is not None and case is not None:
        pairs += 1
        _check_gpu_case(gpu, case, out)
    if cooler is not None:
        pairs += _check_cooler(cpu, cooler, case, out)
    if psu is not None:
        pairs += _check_psu(cpu, gpu, psu, out)
    if ssd is not None:
        pairs += _check_storage(mb, ssd, 1 if ssd.spec("interface") == "NVMe" else 0, out)
    if gpu is not None:
        pairs += _check_pcie(cpu, mb, gpu, out)

    # Без дискретной видеокарты процессору нужна встроенная графика
    if cpu is not None and gpu is None:
        igpu = cpu.spec("igpu")
        complete_without_gpu = all(parts.get(c) is not None for c in CATEGORIES if c != "gpu")
        if complete_without_gpu:
            pairs += 1
            if igpu is None:
                _add(out, Severity.UNKNOWN, "igpu_unknown", "Нет данных о встроенной графике процессора.", cpu)
            elif not igpu:
                _add(out, Severity.WARN, "no_video",
                     "Видеокарта не выбрана, а у процессора нет встроенной графики — изображения не будет.", cpu)
        elif igpu is False:
            _add(out, Severity.INFO, "no_video_hint",
                 "У этого процессора нет встроенной графики — понадобится видеокарта.", cpu)

    report.checked_pairs = pairs
    report.estimated_load_w = estimate_load_w(cpu, gpu)
    return report


def format_report(report: CompatReport, parts: Mapping[str, Part | None] | None = None) -> str:
    """Текст отчёта для Telegram (HTML). Сообщения Issue содержат только данные каталога."""
    from html import escape

    lines = [f"{report.emoji} <b>{report.title}</b>"]
    if report.checked_pairs == 0:
        lines.append("\nВыберите минимум две связанные детали (например, процессор и материнскую плату), "
                     "и я проверю их совместимость.")
    groups = (
        (Severity.BAD, "🔴 <b>Несовместимо</b>"),
        (Severity.UNKNOWN, "⚪ <b>Не хватает данных</b>"),
        (Severity.WARN, "🟡 <b>Ограничения</b>"),
        (Severity.INFO, "ℹ️ <b>Заметки</b>"),
        (Severity.OK, "🟢 <b>Проверено, всё в порядке</b>"),
    )
    for severity, title in groups:
        items = report.by_severity(severity)
        if not items:
            continue
        lines.append(f"\n{title}")
        lines.extend(f"• {escape(i.text)}" for i in items)
    if report.estimated_load_w is not None:
        lines.append(f"\n⚡ Расчётная пиковая нагрузка: ~{report.estimated_load_w} Вт")
    if report.missing and report.checked_pairs:
        lines.append("\n➕ Не выбрано: " + ", ".join(report.missing).lower())
    if report.status == Severity.UNKNOWN and report.checked_pairs:
        lines.append("\n⚠️ Пока не хватает данных, поэтому подтвердить полную совместимость я не могу.")
    return "\n".join(lines)
