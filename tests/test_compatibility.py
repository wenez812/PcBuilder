from bot.services.compatibility import Severity, check_compatibility, format_report
from bot.services.parts import Part


def P(cat, name, **specs):
    return Part(id=hash(name) % 100000, category=cat, name=name, specs=specs)


CPU_AM5 = P("cpu", "R5 7500F", socket="AM5", family="Zen 4", mem_types=["DDR5"], max_mem_mhz={"DDR5": 5200},
            max_ram_gb=192, tdp_w=65, max_power_w=88, pcie_gen=5, igpu=False)
CPU_LGA = P("cpu", "i5-12400F", socket="LGA1700", family="Alder Lake", mem_types=["DDR4", "DDR5"],
            max_mem_mhz={"DDR4": 3200, "DDR5": 4800}, tdp_w=65, max_power_w=117, pcie_gen=5, igpu=False)
CPU_ZEN5 = P("cpu", "R7 9700X", socket="AM5", family="Zen 5", mem_types=["DDR5"], tdp_w=65, max_power_w=88)
MB_AM5 = P("motherboard", "B650M", socket="AM5", chipset="B650", form_factor="mATX", memory_type="DDR5",
           ram_slots=4, max_ram_gb=192, pcie_x16_gen=5, m2_slots=2, sata_ports=4)
MB_DDR4 = P("motherboard", "B760 D4", socket="LGA1700", chipset="B760", form_factor="ATX", memory_type="DDR4",
            ram_slots=2, max_ram_gb=64, pcie_x16_gen=4, m2_slots=2, sata_ports=4)
RAM_D5 = P("ram", "32GB D5", type="DDR5", capacity_gb=32, modules=2, speed_mhz=6000)
RAM_D4 = P("ram", "16GB D4", type="DDR4", capacity_gb=16, modules=2, speed_mhz=3200)
GPU = P("gpu", "RTX 5060", tgp_w=145, rec_psu_w=550, pcie_gen=5, pcie_lanes=8, vram_gb=8)
GPU_BIG = P("gpu", "RTX 5090", tgp_w=575, rec_psu_w=1000, pcie_gen=5, pcie_lanes=16, power_connectors=["12V-2x6"])
GPU_LEN = P("gpu", "GPU 300mm", tgp_w=200, length_mm=280, pcie_gen=4, pcie_lanes=16)
PSU_650 = P("psu", "650W", wattage=650, has_12v_2x6=False)
PSU_300 = P("psu", "300W", wattage=300, has_12v_2x6=False)
CASE_M = P("case", "mATX case", form_factors=["mATX", "Mini-ITX"], max_gpu_length_mm=300, max_cooler_height_mm=150)
CASE_BIG = P("case", "ATX case", form_factors=["ATX", "mATX"], max_gpu_length_mm=400, max_cooler_height_mm=170)
COOLER = P("cooler", "AK400", height_mm=155, tdp_w=220, sockets=["AM5", "LGA1700"])
COOLER_AM4 = P("cooler", "AM4 only", height_mm=100, tdp_w=100, sockets=["AM4"])


def codes(report, severity=None):
    return {i.code for i in report.issues if severity is None or i.severity == severity}


def test_socket_mismatch_is_red():
    r = check_compatibility({"cpu": CPU_AM5, "motherboard": MB_DDR4})
    assert r.status == Severity.BAD and "socket" in codes(r, Severity.BAD)
    assert "не подходит" in format_report(r)


def test_socket_match_green():
    r = check_compatibility({"cpu": CPU_AM5, "motherboard": MB_AM5})
    assert r.status == Severity.OK


def test_missing_socket_is_unknown_never_green():
    cpu = P("cpu", "mystery")
    r = check_compatibility({"cpu": cpu, "motherboard": MB_AM5})
    assert r.status == Severity.UNKNOWN


def test_bios_update_warning():
    r = check_compatibility({"cpu": CPU_ZEN5, "motherboard": MB_AM5})
    assert r.status == Severity.WARN and "bios" in codes(r)


def test_unknown_chipset_gives_unknown():
    mb = P("motherboard", "weird", socket="AM5", chipset="Q999", form_factor="ATX")
    r = check_compatibility({"cpu": CPU_AM5, "motherboard": mb})
    assert "chipset_unknown" in codes(r) and r.status == Severity.UNKNOWN
    wrong = P("motherboard", "wrong", socket="AM5", chipset="B760", form_factor="ATX")
    assert "chipset" in codes(check_compatibility({"cpu": CPU_AM5, "motherboard": wrong}), Severity.BAD)


def test_ram_type_mismatch():
    r = check_compatibility({"cpu": CPU_AM5, "motherboard": MB_AM5, "ram": RAM_D4})
    assert r.status == Severity.BAD and {"ram_type", "cpu_ram_type"} <= codes(r, Severity.BAD)


def test_ram_slots_and_capacity_limit():
    big = P("ram", "128GB", type="DDR4", capacity_gb=128, modules=4, speed_mhz=3200)
    r = check_compatibility({"cpu": CPU_LGA, "motherboard": MB_DDR4, "ram": big})
    assert {"ram_slots", "ram_capacity"} <= codes(r, Severity.BAD)


def test_ram_speed_above_cpu_limit_is_info_only():
    r = check_compatibility({"cpu": CPU_AM5, "motherboard": MB_AM5, "ram": RAM_D5})
    assert "ram_speed" in codes(r, Severity.INFO)
    assert r.status == Severity.OK  # INFO статус не ухудшает


def test_case_form_factor():
    r = check_compatibility({"motherboard": MB_DDR4, "case": CASE_M})
    assert "form" in codes(r, Severity.BAD)
    assert check_compatibility({"motherboard": MB_AM5, "case": CASE_M}).status == Severity.OK


def test_gpu_length_known_and_unknown():
    assert "gpu_length" in codes(check_compatibility({"gpu": P("gpu", "long", length_mm=420), "case": CASE_M}), Severity.BAD)
    unknown_len = check_compatibility({"gpu": GPU, "case": CASE_M})
    assert unknown_len.status == Severity.WARN  # не подтверждаем, пока длина модели неизвестна
    assert check_compatibility({"gpu": GPU_LEN, "case": CASE_M}).status == Severity.OK


def test_cooler_checks():
    assert "cooler_height" in codes(check_compatibility({"cooler": COOLER, "case": CASE_M}), Severity.BAD)
    assert "cooler_socket" in codes(check_compatibility({"cpu": CPU_AM5, "cooler": COOLER_AM4}), Severity.BAD)
    weak = P("cooler", "weak", height_mm=100, tdp_w=60, sockets=["AM5"])
    assert "cooler_tdp" in codes(check_compatibility({"cpu": CPU_AM5, "cooler": weak}), Severity.BAD)
    marginal = P("cooler", "marginal", height_mm=100, tdp_w=75, sockets=["AM5"])
    assert "cooler_tdp_peak" in codes(check_compatibility({"cpu": CPU_AM5, "cooler": marginal}), Severity.WARN)
    assert check_compatibility({"cpu": CPU_AM5, "cooler": COOLER, "case": CASE_BIG}).status == Severity.OK


def test_psu_power():
    low = check_compatibility({"cpu": CPU_AM5, "gpu": GPU, "psu": PSU_300})
    assert "psu_power" in codes(low, Severity.BAD)
    assert low.estimated_load_w == 88 + 145 + 100
    ok = check_compatibility({"cpu": CPU_AM5, "gpu": GPU, "psu": PSU_650})
    assert "psu_power" in codes(ok, Severity.OK)


def test_psu_below_gpu_recommendation():
    psu = P("psu", "500W", wattage=500, has_12v_2x6=False)
    r = check_compatibility({"cpu": CPU_AM5, "gpu": GPU, "psu": psu})
    assert "psu_recommended" in codes(r, Severity.WARN)


def test_psu_12v_connector():
    psu = P("psu", "1000W old", wattage=1000, has_12v_2x6=False)
    assert "psu_12v" in codes(check_compatibility({"cpu": CPU_AM5, "gpu": GPU_BIG, "psu": psu}), Severity.WARN)
    psu_new = P("psu", "1000W new", wattage=1200, has_12v_2x6=True)
    assert "psu_12v" in codes(check_compatibility({"cpu": CPU_AM5, "gpu": GPU_BIG, "psu": psu_new}), Severity.OK)


def test_missing_power_data_is_unknown():
    r = check_compatibility({"cpu": P("cpu", "x"), "gpu": GPU, "psu": PSU_650})
    assert "psu_unknown" in codes(r, Severity.UNKNOWN)


def test_storage_slots():
    nvme = P("ssd", "nvme", interface="NVMe", capacity_gb=1000)
    no_m2 = P("motherboard", "no m2", socket="AM5", m2_slots=0, sata_ports=4)
    assert "m2" in codes(check_compatibility({"motherboard": no_m2, "ssd": nvme}), Severity.BAD)
    assert "m2" in codes(check_compatibility({"motherboard": MB_AM5, "ssd": nvme}), Severity.OK)
    assert "sata_unknown" in codes(check_compatibility({"motherboard": P("motherboard", "m"), "ssd": P("ssd", "s", interface="SATA")}))


def test_pcie_limit_levels():
    old_board = P("motherboard", "pcie3", pcie_x16_gen=3)
    r = check_compatibility({"motherboard": old_board, "gpu": GPU})
    assert "pcie_limit" in codes(r, Severity.WARN)  # x8-карта на PCIe 3.0
    r2 = check_compatibility({"motherboard": MB_DDR4, "gpu": GPU})
    assert "pcie_limit" in codes(r2, Severity.INFO)


def test_no_video_output():
    # частичный набор — только подсказка, статус не ухудшается
    partial = check_compatibility({"cpu": CPU_AM5, "motherboard": MB_AM5})
    assert "no_video_hint" in codes(partial, Severity.INFO) and partial.status == Severity.OK
    # полный набор без видеокарты — реальная проблема
    full = {"cpu": CPU_AM5, "motherboard": MB_AM5, "ram": RAM_D5, "ssd": P("ssd", "s", interface="NVMe"),
            "psu": PSU_650, "case": CASE_BIG, "cooler": COOLER}
    assert "no_video" in codes(check_compatibility(full), Severity.WARN)


def test_single_part_has_no_verdict():
    r = check_compatibility({"cpu": CPU_AM5, "case": None})
    assert r.checked_pairs >= 0
    empty = check_compatibility({})
    assert empty.status == Severity.UNKNOWN
    assert "минимум две" in format_report(empty)


def test_report_text_escapes_html():
    cpu = P("cpu", "<b>x</b>", socket="AM5<", )
    mb = P("motherboard", "m", socket="AM4")
    assert "<b>x" not in format_report(check_compatibility({"cpu": cpu, "motherboard": mb}))
