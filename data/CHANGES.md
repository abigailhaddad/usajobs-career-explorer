# Pipeline run — 2026-08-28 18:05 UTC

- snapshotted: 17 previous tables
- stage 1 (quiz): ok
- stage 2 (openings): ok
- stage 3 (hires): ok
- stage 7 (standards): ok
- stage 4 (build): ok
- stage 5 (instrument): ok
- stage 4 (build), again: ok

## Changes since the previous run

### questions
- rows: 32 → 32
- no changes

### series_profiles
- rows: 302 → 302
- no changes

### openings
- rows: 605 → 605
- `ann_reachable`: total 18173 → 18112 (-61); 16 rows changed
  - 0801: 103 → 90 (-13)
  - 1550: 20 → 13 (-7)
  - 1301: 27 → 21 (-6)
  - 0855: 28 → 23 (-5)
  - 0601: 75 → 71 (-4)
- `openings_reachable`: total 29236 → 29168 (-68); 16 rows changed
  - 0801: 136 → 123 (-13)
  - 1550: 22 → 14 (-8)
  - 0893: 11 → 4 (-7)
  - 0855: 32 → 26 (-6)
  - 1301: 58 → 52 (-6)
- `pct_degree_required`: total 1,377.9 → 1,458.9 (+81); 8 rows changed
  - 1501: 33.3 → 100 (+66.7)
  - 1560: 22.2 → 28.6 (+6.4)
  - 1550: 5 → 7.7 (+2.7)
  - 1301: 7.4 → 9.5 (+2.1)
  - 0855: 7.1 → 8.7 (+1.6)
- `pct_education_substitutable`: total 12,071.6 → 12,063.7 (-7.9); 13 rows changed
  - 0893: 50 → 75 (+25)
  - 0854: 38.5 → 27.3 (-11.2)
  - 1515: 66.7 → 55.6 (-11.1)
  - 0801: 35 → 27.8 (-7.2)
  - 0896: 40 → 33.3 (-6.7)

### hires
- rows: 645 → 645
- `hires_entry_perm`: total 512450 → 506683 (-5767); 57 rows changed
  - 1550: 2570 → 1406 (-1,164)
  - 0830: 3061 → 1928 (-1,133)
  - 0801: 1700 → 1120 (-580)
  - 0855: 1588 → 1043 (-545)
  - 0854: 614 → 201 (-413)

### series_facts
- rows: 302 → 302
- `flag_count`: total 337 → 337 (0); 2 rows changed
  - 0854: 1 → 2 (+1)
  - 0871: 2 → 1 (-1)
- `hires_entry_perm`: total 482681 → 477022 (-5659); 47 rows changed
  - 1550: 2570 → 1406 (-1,164)
  - 0830: 3061 → 1928 (-1,133)
  - 0801: 1700 → 1120 (-580)
  - 0855: 1588 → 1043 (-545)
  - 0854: 614 → 201 (-413)
- `pct_degree_required`: total 865.5 → 879.8 (+14.3); 7 rows changed
  - 1560: 22.2 → 28.6 (+6.4)
  - 1550: 5 → 7.7 (+2.7)
  - 1301: 7.4 → 9.5 (+2.1)
  - 0855: 7.1 → 8.7 (+1.6)
  - 1320: 12.5 → 13.3 (+0.8)

### opm_standards
- rows: 415 → 415
- no changes

### hires_by_state
- rows: 9,703 → 9,697  (+0 added, -6 removed)
  - removed: 0405 | REDACTED, 0413 | REDACTED, 0470 | REDACTED, 0602 | REDACTED, 0701 | REDACTED, 1501 | REDACTED
- `entry_hires`: total 512433 → 506683 (-5750); 51 rows changed
  - 1550 | REDACTED: 2506 → 1342 (-1,164)
  - 0830 | REDACTED: 3010 → 1877 (-1,133)
  - 0801 | REDACTED: 1358 → 778 (-580)
  - 0855 | REDACTED: 1542 → 997 (-545)
  - 0854 | REDACTED: 611 → 198 (-413)

### hires_by_month
- rows: 28,841 → 28,841
- `entry_hires`: total 512162 → 506397 (-5765); 1103 rows changed
  - 0830 | 202307: 205 → 130 (-75)
  - 0830 | 202208: 146 → 75 (-71)
  - 1550 | 202307: 154 → 85 (-69)
  - 0830 | 202209: 111 → 50 (-61)
  - 1550 | 202407: 151 → 92 (-59)

### generated_questions
- rows: 24 → 24
- `text`: 24 rows changed value
- `axis`: 19 rows changed value
- `hiring_weighted_var`: total 14.6 → 16.2 (+1.5); 24 rows changed
  - 3: 1.1 → 1.3 (+0.2)
  - 9: 0.6 → 0.7 (+0.2)
  - 11: 0.4 → 0.6 (+0.2)
  - 14: 0.4 → 0.5 (+0.1)
  - 13: 0.4 → 0.5 (+0.1)

### family_questions
- rows: 24 → 24
- `text`: 20 rows changed value

### mixed_questions
- rows: 25 → 25
- `text`: 25 rows changed value
- `origin`: 6 rows changed value

