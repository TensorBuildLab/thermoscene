# Zone sweep: DOE reference medium office

One scene per conditioned zone of the unmodified model (`RefBldgMediumOfficeNew2004_Chicago.idf`), one shared EnergyPlus run. **15 / 15 zones built.** Rooftop equipment is generated only where the scene contains roof surfaces (top floor).

| Zone | Plenum | EnergyPlus surfaces | Traced components | Diffusers / grilles | Plenum elements | Rooftop elements | Surface temperatures sim / phys / heur | Build time (s) |
|---|---|---|---|---|---|---|---|---|
| Core_bottom | FirstFloor_Plenum | 24 | 10 | 40 / 20 | 45 | 0 | 24 / 45 / 60 | 8.8 |
| Core_mid | MidFloor_Plenum | 24 | 10 | 40 / 20 | 45 | 0 | 24 / 45 / 60 | 6.3 |
| Core_top | TopFloor_Plenum | 21 | 10 | 40 / 20 | 45 | 10 | 21 / 45 / 70 | 7.3 |
| Perimeter_bot_ZN_1 | FirstFloor_Plenum | 27 | 10 | 9 / 4 | 14 | 0 | 27 / 14 / 13 | 1.6 |
| Perimeter_bot_ZN_2 | FirstFloor_Plenum | 27 | 10 | 7 / 3 | 12 | 0 | 27 / 12 / 10 | 1.4 |
| Perimeter_bot_ZN_3 | FirstFloor_Plenum | 27 | 10 | 9 / 4 | 14 | 0 | 27 / 14 / 13 | 1.7 |
| Perimeter_bot_ZN_4 | FirstFloor_Plenum | 27 | 10 | 7 / 3 | 12 | 0 | 27 / 12 / 10 | 1.7 |
| Perimeter_mid_ZN_1 | MidFloor_Plenum | 27 | 10 | 9 / 4 | 14 | 0 | 27 / 14 / 13 | 1.8 |
| Perimeter_mid_ZN_2 | MidFloor_Plenum | 27 | 10 | 8 / 4 | 13 | 0 | 27 / 13 / 12 | 1.6 |
| Perimeter_mid_ZN_3 | MidFloor_Plenum | 27 | 10 | 9 / 4 | 14 | 0 | 27 / 14 / 13 | 1.7 |
| Perimeter_mid_ZN_4 | MidFloor_Plenum | 27 | 10 | 8 / 4 | 13 | 0 | 27 / 13 / 12 | 1.6 |
| Perimeter_top_ZN_1 | TopFloor_Plenum | 24 | 10 | 9 / 4 | 14 | 10 | 24 / 14 / 23 | 1.7 |
| Perimeter_top_ZN_2 | TopFloor_Plenum | 24 | 10 | 8 / 4 | 13 | 10 | 24 / 13 / 22 | 2.0 |
| Perimeter_top_ZN_3 | TopFloor_Plenum | 24 | 10 | 9 / 4 | 14 | 10 | 24 / 14 / 23 | 1.7 |
| Perimeter_top_ZN_4 | TopFloor_Plenum | 24 | 10 | 9 / 4 | 14 | 10 | 24 / 14 / 23 | 1.7 |
