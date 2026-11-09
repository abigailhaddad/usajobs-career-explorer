# Pipeline run — 2026-11-09 22:15 UTC

- snapshotted: 16 previous tables
- stage 1 (quiz): ok
- stage 2 (openings): ok
- stage 3 (hires): ok
- stage 4 (build): ok

## Changes since the previous run

### questions
- rows: 32 → 32
- no changes

### series_profiles
- rows: 302 → 302
- no changes

### openings
- rows: 610 → 611  (+1 added, -0 removed)
  - added: 1981
- `ann_reachable`: total 26338 → 27338 (1000); 180 rows changed
  - 0679: 2313 → 2432 (+119)
  - 0620: 1730 → 1793 (+63)
  - 0640: 1416 → 1470 (+54)
  - 0621: 1035 → 1084 (+49)
  - 0303: 962 → 1004 (+42)
- `openings_reachable`: total 40089 → 41635 (1546); 180 rows changed
  - 0679: 3101 → 3257 (+156)
  - 1801: 451 → 555 (+104)
  - 0620: 2043 → 2113 (+70)
  - 0640: 1669 → 1726 (+57)
  - 0621: 1367 → 1423 (+56)
- `reachable_open_now`: total 2286 → 2436 (150); 189 rows changed
  - 0679: 193 → 213 (+20)
  - 0621: 85 → 96 (+11)
  - 0318: 5 → 15 (+10)
  - 7408: 45 → 54 (+9)
  - 0101: 6 → 14 (+8)
- `status`: 52 rows changed value
- `pct_degree_required`: total 1,251.4 → 1,240.6 (-10.8); 45 rows changed
  - 1373: 16.7 → 14.3 (-2.4)
  - 1560: 16.7 → 14.3 (-2.4)
  - 0855: 8.3 → 10.3 (+2)
  - 0341: 19 → 17.4 (-1.6)
  - 0503: 0 → 1.5 (+1.5)
- `pct_education_substitutable`: total 12,918.1 → 12,945.8 (+27.7); 98 rows changed
  - 0603: 0 → 33.3 (+33.3)
  - 1654: 33.3 → 25 (-8.3)
  - 0893: 62.5 → 55.6 (-6.9)
  - 0391: 44.4 → 50 (+5.6)
  - 0341: 61.9 → 56.5 (-5.4)

### hires
- rows: 647 → 647
- `hires_entry_perm`: total 517444 → 522987 (5543); 290 rows changed
  - 1896: 9329 → 9656 (+327)
  - 0679: 36780 → 37053 (+273)
  - 0962: 35828 → 36027 (+199)
  - 3566: 14220 → 14403 (+183)
  - 1102: 8198 → 8360 (+162)
- `hires_new`: total 1315800 → 1337517 (21717); 428 rows changed
  - 0610: 59317 → 60786 (+1,469)
  - 2210: 40856 → 41560 (+704)
  - 0301: 50137 → 50835 (+698)
  - 0303: 61393 → 62071 (+678)
  - 0962: 37436 → 38056 (+620)

### series_facts
- rows: 302 → 302
- `flag_count`: total 342 → 339 (-3); 36 rows changed
  - 0861: 2 → 0 (-2)
  - 0150: 3 → 2 (-1)
  - 0308: 1 → 2 (+1)
  - 0188: 2 → 1 (-1)
  - 0335: 1 → 0 (-1)
- `status`: 34 rows changed value
- `hires_entry_perm`: total 487074 → 492375 (5301); 221 rows changed
  - 1896: 9329 → 9656 (+327)
  - 0679: 36780 → 37053 (+273)
  - 0962: 35828 → 36027 (+199)
  - 3566: 14220 → 14403 (+183)
  - 1102: 8198 → 8360 (+162)
- `reachable_open_now`: total 2,070 → 2,217 (+147); 152 rows changed
  - 0679: 193 → 213 (+20)
  - 0621: 85 → 96 (+11)
  - 0318: 5 → 15 (+10)
  - 7408: 45 → 54 (+9)
  - 0101: 6 → 14 (+8)
- `pct_degree_required`: total 811.7 → 803.7 (-8); 41 rows changed
  - 1560: 16.7 → 14.3 (-2.4)
  - 0855: 8.3 → 10.3 (+2)
  - 0341: 19 → 17.4 (-1.6)
  - 0503: 0 → 1.5 (+1.5)
  - 1320: 15.8 → 15 (-0.8)

### opm_standards
- rows: 415 → 415
- no changes

### hires_by_state
- rows: 9,752 → 9,767  (+15 added, -0 removed)
  - added: 0244 | WASHINGTON, 0456 | WISCONSIN, 0669 | FLORIDA, 0673 | MICHIGAN, 0861 | MISSISSIPPI, 1825 | HAWAII, 1850 | NEBRASKA, 1910 | FLORIDA, 2001 | NORTH CAROLINA, 4607 | DELAWARE, 4701 | GEORGIA, 4804 | OKLAHOMA
- `entry_hires`: total 517444 → 522971 (5527); 1069 rows changed
  - 1896 | REDACTED: 9329 → 9656 (+327)
  - 0679 | REDACTED: 3757 → 3913 (+156)
  - 1102 | REDACTED: 6712 → 6866 (+154)
  - 0085 | REDACTED: 3676 → 3790 (+114)
  - 1801 | REDACTED: 10987 → 11097 (+110)

### hires_by_month
- rows: 29,658 → 30,067  (+409 added, -0 removed)
  - added: * | 202609, 0006 | 202609, 0007 | 202609, 0017 | 202609, 0018 | 202609, 0020 | 202609, 0025 | 202609, 0028 | 202609, 0029 | 202609, 0030 | 202609, 0060 | 202609, 0080 | 202609
- `entry_hires`: total 517158 → 518688 (1530); 510 rows changed
  - 1801 | 202512: 1664 → 1693 (+29)
  - 0085 | 202608: 69 → 95 (+26)
  - 0840 | 202608: 7 → 33 (+26)
  - 0830 | 202608: 52 → 76 (+24)
  - 3806 | 202608: 36 → 59 (+23)
- `new_hires`: total 1315016 → 1318674 (3658); 973 rows changed
  - 0610 | 202608: 1080 → 1158 (+78)
  - 2210 | 202608: 595 → 664 (+69)
  - 0301 | 202608: 535 → 600 (+65)
  - 0830 | 202608: 132 → 186 (+54)
  - 0343 | 202608: 244 → 295 (+51)

### generated_questions
- rows: 24 → 24
- no changes

### family_questions
- rows: 24 → 24
- no changes

### mixed_questions
- rows: 25 → 25
- no changes

