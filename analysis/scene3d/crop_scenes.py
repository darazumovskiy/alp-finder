#!/usr/bin/env python3
"""Обрезка сплат-сцен рабочим радиусом: фон-за-километры вон.

SfM зоны реконструирует и дальние склоны из кадров; привязка-подобие точна
только в ядре зоны, фон ложится с промахом в сотни метров и километры,
размазываясь по карте («месиво», жалоба оператора 19.08). Здесь каждая
сцена режется сферой радиуса R метров вокруг якоря геопривязки: сплат
переводится в ENU через T из geo.json, дальние выбрасываются, части
пишутся заново, манифест обновляется.

Запуск: crop_scenes.py         # все сцены манифеста, радиусы по типу
"""
import json
from pathlib import Path

import numpy as np

SCENES = Path("/Users/d.razumovskiy/work/alp-finder/analysis/viewer/scenes")
RADIUS = {"koshki": 260.0, "zona": 1500.0}
R_DEFAULT = 450.0                 # зоны линии падения (400 м + запас)


def main():
    man_p = SCENES / "manifest.json"
    man = json.loads(man_p.read_text())
    for sc in man["scenes"]:
        r_m = RADIUS.get(sc["id"], R_DEFAULT)
        buf = b"".join((SCENES / Path(p).name).read_bytes() for p in sc["parts"])
        n = len(buf) // 32
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(n, 32)
        pos = arr[:, :12].copy().view("<f4").reshape(n, 3)
        T = np.array(sc["geo"]["T"]).reshape(4, 4)
        enu = pos @ T[:3, :3].T + T[:3, 3]
        keep = np.linalg.norm(enu, axis=1) <= r_m
        kept = arr[keep]
        print(f"{sc['id']}: {n} → {keep.sum()} сплатов (радиус {r_m:.0f} м)")
        for p in sc["parts"]:
            (SCENES / Path(p).name).unlink(missing_ok=True)
        data = kept.tobytes()
        parts = []
        for k in range(0, len(data), 24 * 2 ** 20):
            pn = f"{sc['id']}_c{len(parts)}"
            (SCENES / pn).write_bytes(data[k:k + 24 * 2 ** 20])
            parts.append("scenes/" + pn)
        sc["parts"] = parts
    man_p.write_text(json.dumps(man))
    print("манифест обновлён")


if __name__ == "__main__":
    main()
