"""Стартовый каталог: ТОЛЬКО технические характеристики. Цен здесь нет и быть не должно.

Как читать данные:
* Числовые лимиты (сокет, TDP, тип памяти, размеры корпуса и т.п.) — паспортные характеристики
  производителей, внесены вручную; перед закупкой проверяйте их на странице производителя.
* Поля `gaming`, `multi`, `compute` у процессоров и видеокарт — РЕДАКТОРСКИЙ класс производительности
  по шкале 0–10. Это не замеры и не FPS: он нужен только алгоритму подбора, чтобы сравнивать
  детали внутри каталога. Реальные FPS живут отдельно, в таблице бенчмарков с источниками.
* Если характеристика неизвестна, поля просто нет — проверка совместимости вернёт ⚪.
* Для видеокарт указаны характеристики чипа/референса; длина и разъёмы зависят от модели
  партнёра (ASUS, MSI и т.д.), поэтому здесь они не указаны и проверяются как «есть ограничения».
"""
from __future__ import annotations

from typing import Any

SOURCE_NOTE = "Паспортные данные производителя, внесено вручную. Проверьте перед покупкой."

Entry = dict[str, Any]


def _mem_limits(family: str, mem_types: list[str], given: int | None) -> dict[str, int]:
    """Официальная максимальная частота памяти по типам (МГц)."""
    if len(mem_types) == 1:
        return {mem_types[0]: given} if given else {}
    if given is None:  # Alder Lake / Raptor Lake без разгона памяти
        return {"DDR4": 3200, "DDR5": 4800}
    return {"DDR4": 3200, "DDR5": given}


def _cpu(brand, name, socket, cores, threads, tdp, max_power, mem_types, max_mem, max_ram, igpu,
         family, gaming, multi, pcie=5) -> Entry:
    return dict(category="cpu", brand=brand, name=name, specs=dict(
        socket=socket, cores=cores, threads=threads, tdp_w=tdp, max_power_w=max_power,
        mem_types=mem_types, max_mem_mhz=_mem_limits(family, mem_types, max_mem),
        max_ram_gb=max_ram, igpu=igpu, family=family,
        pcie_gen=pcie, gaming=gaming, multi=multi))


def _gpu(brand, name, vram, tgp, rec_psu, pcie_gen, lanes, gaming, compute, connectors=None) -> Entry:
    specs = dict(vram_gb=vram, tgp_w=tgp, rec_psu_w=rec_psu, pcie_gen=pcie_gen, pcie_lanes=lanes,
                 gaming=gaming, compute=compute)
    if connectors:
        specs["power_connectors"] = connectors
    return dict(category="gpu", brand=brand, name=name, specs=specs)


def _board(brand, name, socket, chipset, form, mem_type, slots, max_ram, pcie_gen) -> Entry:
    return dict(category="motherboard", brand=brand, name=name, specs=dict(
        socket=socket, chipset=chipset, form_factor=form, memory_type=mem_type, ram_slots=slots,
        max_ram_gb=max_ram, pcie_x16_gen=pcie_gen,
        # Минимум, который гарантированно есть на платах этих чипсетов
        m2_slots=2, sata_ports=4))


def _ram(brand, name, mem_type, capacity, modules, speed) -> Entry:
    return dict(category="ram", brand=brand, name=name, specs=dict(
        type=mem_type, capacity_gb=capacity, modules=modules, speed_mhz=speed))


def _ssd(brand, name, capacity, interface, form, pcie_gen=None) -> Entry:
    specs = dict(capacity_gb=capacity, interface=interface, form_factor=form)
    if pcie_gen:
        specs["pcie_gen"] = pcie_gen
    return dict(category="ssd", brand=brand, name=name, specs=specs)


def _psu(brand, name, watts, efficiency, atx3=False, has_12v_2x6=False) -> Entry:
    return dict(category="psu", brand=brand, name=name, specs=dict(
        wattage=watts, efficiency=efficiency, form_factor="ATX", has_12v_2x6=has_12v_2x6,
        atx3=atx3,
        # Гарантированный минимум разъёмов PCIe 8-pin у блоков такой мощности и линейки
        pcie_8pin_min=2))


def _case(brand, name, forms, gpu_len, cooler_h) -> Entry:
    return dict(category="case", brand=brand, name=name, specs=dict(
        form_factors=forms, max_gpu_length_mm=gpu_len, max_cooler_height_mm=cooler_h))


def _cooler(brand, name, height, tdp, sockets) -> Entry:
    return dict(category="cooler", brand=brand, name=name, specs=dict(
        height_mm=height, tdp_w=tdp, sockets=sockets))


_ALL_ATX = ["ATX", "mATX", "Mini-ITX"]
_M_ATX = ["mATX", "Mini-ITX"]
_INTEL_AM = ["AM4", "AM5", "LGA1700", "LGA1851", "LGA1200", "LGA1151"]

SEED_COMPONENTS: list[Entry] = [
    # ---------------- Процессоры ----------------
    _cpu("AMD", "AMD Ryzen 5 5600", "AM4", 6, 12, 65, 88, ["DDR4"], 3200, 128, False, "Zen 3", 5.0, 4.5, 4),
    _cpu("AMD", "AMD Ryzen 7 5700X", "AM4", 8, 16, 65, 88, ["DDR4"], 3200, 128, False, "Zen 3", 5.5, 6.0, 4),
    _cpu("AMD", "AMD Ryzen 7 5700X3D", "AM4", 8, 16, 105, 142, ["DDR4"], 3200, 128, False, "Zen 3", 6.8, 6.0, 4),
    _cpu("AMD", "AMD Ryzen 5 7500F", "AM5", 6, 12, 65, 88, ["DDR5"], 5200, 192, False, "Zen 4", 6.5, 5.5),
    _cpu("AMD", "AMD Ryzen 5 7600", "AM5", 6, 12, 65, 88, ["DDR5"], 5200, 192, True, "Zen 4", 6.6, 5.6),
    _cpu("AMD", "AMD Ryzen 7 7700", "AM5", 8, 16, 65, 88, ["DDR5"], 5200, 192, True, "Zen 4", 7.0, 7.2),
    _cpu("AMD", "AMD Ryzen 7 7800X3D", "AM5", 8, 16, 120, 162, ["DDR5"], 5200, 192, True, "Zen 4", 8.8, 7.3),
    _cpu("AMD", "AMD Ryzen 5 9600X", "AM5", 6, 12, 65, 88, ["DDR5"], 5600, 192, True, "Zen 5", 7.2, 6.0),
    _cpu("AMD", "AMD Ryzen 7 9700X", "AM5", 8, 16, 65, 88, ["DDR5"], 5600, 192, True, "Zen 5", 7.6, 7.4),
    _cpu("AMD", "AMD Ryzen 7 9800X3D", "AM5", 8, 16, 120, 162, ["DDR5"], 5600, 192, True, "Zen 5", 9.6, 7.8),
    _cpu("Intel", "Intel Core i3-12100F", "LGA1700", 4, 8, 58, 89, ["DDR4", "DDR5"], None, 128, False, "Alder Lake", 4.2, 3.2),
    _cpu("Intel", "Intel Core i5-12400F", "LGA1700", 6, 12, 65, 117, ["DDR4", "DDR5"], None, 128, False, "Alder Lake", 5.3, 4.8),
    _cpu("Intel", "Intel Core i5-13400F", "LGA1700", 10, 16, 65, 148, ["DDR4", "DDR5"], None, 128, False, "Raptor Lake", 5.8, 6.0),
    _cpu("Intel", "Intel Core i5-14600K", "LGA1700", 14, 20, 125, 181, ["DDR4", "DDR5"], 5600, 192, True, "Raptor Lake Refresh", 7.5, 7.6),
    _cpu("Intel", "Intel Core i7-14700K", "LGA1700", 20, 28, 125, 253, ["DDR4", "DDR5"], 5600, 192, True, "Raptor Lake Refresh", 8.2, 8.6),
    _cpu("Intel", "Intel Core Ultra 5 245K", "LGA1851", 14, 14, 125, 159, ["DDR5"], 6400, 192, True, "Arrow Lake", 7.0, 7.2),
    _cpu("Intel", "Intel Core Ultra 7 265K", "LGA1851", 20, 20, 125, 250, ["DDR5"], 6400, 192, True, "Arrow Lake", 7.9, 8.5),

    # ---------------- Видеокарты (референсные данные чипа) ----------------
    _gpu("Intel", "Intel Arc B570 10GB", 10, 150, None, 4, 8, 4.2, 4.0),
    _gpu("Intel", "Intel Arc B580 12GB", 12, 190, None, 4, 8, 5.2, 5.0),
    _gpu("NVIDIA", "NVIDIA GeForce RTX 4060 8GB", 8, 115, 550, 4, 8, 4.6, 4.6),
    _gpu("AMD", "AMD Radeon RX 7600 8GB", 8, 165, 550, 4, 8, 4.3, 3.8),
    _gpu("NVIDIA", "NVIDIA GeForce RTX 5050 8GB", 8, 130, 550, 5, 8, 4.4, 4.4),
    _gpu("NVIDIA", "NVIDIA GeForce RTX 5060 8GB", 8, 145, 550, 5, 8, 5.4, 5.4),
    _gpu("AMD", "AMD Radeon RX 9060 XT 16GB", 16, 160, None, 5, 16, 5.9, 5.2),
    _gpu("NVIDIA", "NVIDIA GeForce RTX 5060 Ti 16GB", 16, 180, 600, 5, 8, 6.0, 6.0),
    _gpu("NVIDIA", "NVIDIA GeForce RTX 4070 SUPER 12GB", 12, 220, 650, 4, 16, 6.8, 6.8),
    _gpu("NVIDIA", "NVIDIA GeForce RTX 5070 12GB", 12, 250, 650, 5, 16, 7.0, 7.0),
    _gpu("AMD", "AMD Radeon RX 7800 XT 16GB", 16, 263, 700, 4, 16, 6.6, 5.8),
    _gpu("AMD", "AMD Radeon RX 9070 16GB", 16, 220, 650, 5, 16, 7.4, 6.6),
    _gpu("AMD", "AMD Radeon RX 9070 XT 16GB", 16, 304, 750, 5, 16, 7.9, 7.0),
    _gpu("NVIDIA", "NVIDIA GeForce RTX 5070 Ti 16GB", 16, 300, 750, 5, 16, 8.2, 8.4),
    _gpu("NVIDIA", "NVIDIA GeForce RTX 5080 16GB", 16, 360, 850, 5, 16, 9.0, 9.2),
    _gpu("NVIDIA", "NVIDIA GeForce RTX 5090 32GB", 32, 575, 1000, 5, 16, 10.0, 10.0, ["12V-2x6"]),

    # ---------------- Материнские платы ----------------
    _board("Gigabyte", "Gigabyte B550M DS3H", "AM4", "B550", "mATX", "DDR4", 4, 128, 4),
    _board("MSI", "MSI MAG B550M Mortar", "AM4", "B550", "mATX", "DDR4", 4, 128, 4),
    _board("ASRock", "ASRock B650M-HDV/M.2", "AM5", "B650", "mATX", "DDR5", 2, 96, 4),
    _board("Gigabyte", "Gigabyte B650M Aorus Elite AX", "AM5", "B650", "mATX", "DDR5", 4, 192, 5),
    _board("ASUS", "ASUS TUF Gaming B650-Plus WiFi", "AM5", "B650", "ATX", "DDR5", 4, 192, 5),
    _board("MSI", "MSI MAG B650 Tomahawk WiFi", "AM5", "B650", "ATX", "DDR5", 4, 192, 5),
    _board("MSI", "MSI PRO B760M-P DDR4", "LGA1700", "B760", "mATX", "DDR4", 2, 64, 4),
    _board("ASUS", "ASUS Prime B760M-A WiFi D4", "LGA1700", "B760", "mATX", "DDR4", 4, 128, 5),
    _board("MSI", "MSI MAG B760 Tomahawk WiFi DDR4", "LGA1700", "B760", "ATX", "DDR4", 4, 128, 5),
    _board("MSI", "MSI PRO Z790-P WiFi", "LGA1700", "Z790", "ATX", "DDR5", 4, 192, 5),
    _board("MSI", "MSI PRO Z890-P WiFi", "LGA1851", "Z890", "ATX", "DDR5", 4, 192, 5),

    # ---------------- Оперативная память ----------------
    _ram("Kingston", "Kingston FURY Beast 16GB (2x8GB) DDR4-3200", "DDR4", 16, 2, 3200),
    _ram("Kingston", "Kingston FURY Beast 32GB (2x16GB) DDR4-3200", "DDR4", 32, 2, 3200),
    _ram("G.Skill", "G.Skill Ripjaws V 32GB (2x16GB) DDR4-3600", "DDR4", 32, 2, 3600),
    _ram("Kingston", "Kingston FURY Beast 16GB (2x8GB) DDR5-5600", "DDR5", 16, 2, 5600),
    _ram("Kingston", "Kingston FURY Beast 32GB (2x16GB) DDR5-5600", "DDR5", 32, 2, 5600),
    _ram("Corsair", "Corsair Vengeance 32GB (2x16GB) DDR5-6000 CL30", "DDR5", 32, 2, 6000),
    _ram("Corsair", "Corsair Vengeance 64GB (2x32GB) DDR5-6000 CL30", "DDR5", 64, 2, 6000),

    # ---------------- SSD ----------------
    _ssd("Kingston", "Kingston A400 480GB SATA", 480, "SATA", '2.5"'),
    _ssd("Samsung", "Samsung 870 EVO 1TB SATA", 1000, "SATA", '2.5"'),
    _ssd("Kingston", "Kingston NV3 500GB NVMe", 500, "NVMe", "M.2 2280", 4),
    _ssd("Kingston", "Kingston NV3 1TB NVMe", 1000, "NVMe", "M.2 2280", 4),
    _ssd("Kingston", "Kingston NV3 2TB NVMe", 2000, "NVMe", "M.2 2280", 4),
    _ssd("WD", "WD Blue SN580 1TB NVMe", 1000, "NVMe", "M.2 2280", 4),
    _ssd("Samsung", "Samsung 990 EVO Plus 1TB NVMe", 1000, "NVMe", "M.2 2280", 4),
    _ssd("Samsung", "Samsung 990 EVO Plus 2TB NVMe", 2000, "NVMe", "M.2 2280", 4),

    # ---------------- Блоки питания ----------------
    _psu("Deepcool", "Deepcool PK550D 550W", 550, "80+ Bronze"),
    _psu("Deepcool", "Deepcool PK650D 650W", 650, "80+ Bronze"),
    _psu("Corsair", "Corsair CX650 650W", 650, "80+ Bronze"),
    _psu("Deepcool", "Deepcool PQ750M 750W", 750, "80+ Gold", atx3=True, has_12v_2x6=True),
    _psu("Corsair", "Corsair RM750e 750W", 750, "80+ Gold", atx3=True, has_12v_2x6=True),
    _psu("be quiet!", "be quiet! Pure Power 12 M 750W", 750, "80+ Gold", atx3=True, has_12v_2x6=True),
    _psu("Deepcool", "Deepcool PQ850M 850W", 850, "80+ Gold", atx3=True, has_12v_2x6=True),
    _psu("Corsair", "Corsair RM850e 850W", 850, "80+ Gold", atx3=True, has_12v_2x6=True),
    _psu("be quiet!", "be quiet! Pure Power 12 M 1000W", 1000, "80+ Gold", atx3=True, has_12v_2x6=True),
    _psu("Corsair", "Corsair RM1000e 1000W", 1000, "80+ Gold", atx3=True, has_12v_2x6=True),

    # ---------------- Корпуса ----------------
    _case("Cooler Master", "Cooler Master MasterBox Q300L", _M_ATX, 360, 159),
    _case("Deepcool", "Deepcool CC560", _ALL_ATX, 370, 163),
    _case("Corsair", "Corsair 4000D Airflow", _ALL_ATX, 360, 170),
    _case("NZXT", "NZXT H5 Flow", _ALL_ATX, 365, 165),
    _case("Fractal Design", "Fractal Design Pop Air", _ALL_ATX, 405, 170),
    _case("Lian Li", "Lian Li Lancool 216", _ALL_ATX, 392, 180),
    _case("be quiet!", "be quiet! Pure Base 500DX", _ALL_ATX, 369, 190),

    # ---------------- Охлаждение процессора ----------------
    _cooler("Deepcool", "Deepcool AG400", 150, 220, _INTEL_AM),
    _cooler("Deepcool", "Deepcool AK400", 155, 220, _INTEL_AM),
    _cooler("Thermalright", "Thermalright Peerless Assassin 120 SE", 155, 245, ["AM4", "AM5", "LGA1700", "LGA1851", "LGA1200", "LGA115x"]),
    _cooler("Deepcool", "Deepcool AK620", 160, 260, _INTEL_AM),
]

for _entry in SEED_COMPONENTS:
    _entry.setdefault("condition", "new")
