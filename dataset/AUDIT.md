# Dataset capture audit (Phase 0)

**66 files, 61 unique images** (5 byte-identical duplicates). **48 unique images usable for photometry** under provisional thresholds (hard-clip fraction <= 0.15).

**Forced-stop condition §10 (fewer than 15 usable for photometry): not triggered.**

Method: green-channel Otsu threshold -> largest connected component -> hole fill = plate mask.
Clipping = fraction of in-plate pixels with G >= 255 (near-clip: G >= 250).
Tilt = region orientation distance from axis-aligned (estimate only; Phase 2 measures properly).
Overrun = fraction of each image border covered by plate mask.
px/lane assumes 4 lanes (S/co/R/sd) — inferred, not measured.

| file | WxH px | plate | clip | near-clip | tilt(deg) | overrun T/B/L/R | px/lane | verdict | dup of |
|---|---|---|---|---|---|---|---|---|---|
| MEHQ-P20_1st Step 3hr_21st July26.png | 99x155 | y | 0.1639 | 0.554 | 2.05 | 0.00/0.86/0.00/0.00 | 21.8 | positions_only | - |
| MEHQ-P20_2nd Step 1hr_21st July26.png | 93x182 | y | 0.0456 | 0.2297 | 1.89 | 0.00/0.00/0.15/0.00 | 22.8 | photometry_ok | - |
| MEHQ-P21_1st Step 6hr_22nd July26.png | 84x141 | y | 0.2868 | 0.6657 | 1.13 | 0.00/0.00/0.00/0.00 | 15.2 | positions_only | - |
| MEHQ-P22_1st Step 6hr_22nd July26.png | 62x110 | y | 0.1241 | 0.2902 | 1.71 | 0.00/0.00/0.00/0.00 | 14.5 | photometry_partial | - |
| MEHQ-P22_2nd Step 1hr_22nd July26.png | 99x208 | y | 0.0001 | 0.0776 | 1.12 | 0.00/0.00/0.00/0.00 | 24.0 | photometry_ok | - |
| MEHQ-P23_4hr_24th July26.png | 141x207 | y | 0.0011 | 0.0541 | 3.08 | 0.06/0.37/0.00/0.00 | 32.2 | photometry_ok | - |
| MEHQ-P23_5+2hr_25th July26.png | 73x110 | y | 0.115 | 0.4509 | 0.77 | 0.01/0.00/0.00/0.00 | 17.0 | photometry_partial | - |
| MEHQ-P23_5hr_24th July26.png | 90x142 | y | 0.1717 | 0.4461 | 0.92 | 0.00/0.00/0.09/0.00 | 21.8 | positions_only | - |
| MEHQ-P24_4hr_24th July26.png | 136x219 | y | 0.0 | 0.001 | 0.05 | 0.00/0.00/0.35/0.90 | 34.0 | photometry_ok | - |
| MEHQ-P24_5+2hr_25th July26.png | 96x170 | y | 0.2322 | 0.504 | 3.76 | 0.00/0.23/0.00/0.00 | 22.5 | positions_only | - |
| MEHQ-P24_5hr_24th July26.png | 94x163 | y | 0.2534 | 0.4726 | 1.37 | 0.00/0.00/0.00/0.00 | 20.8 | positions_only | - |
| MEHQ-P25_1+2hr_25th July26.png | 101x170 | y | 0.1729 | 0.305 | 0.32 | 0.00/0.00/0.00/0.00 | 22.2 | positions_only | - |
| MEHQ-P25_1hr_24th July26.png | 120x215 | y | 0.0 | 0.0002 | 0.15 | 0.00/0.00/0.00/0.00 | 27.8 | photometry_ok | - |
| MEHQ-P26_1+2hr_25th July26.png | 129x191 | y | 0.0046 | 0.0799 | 0.94 | 0.00/0.00/0.00/0.53 | 32.0 | photometry_ok | - |
| MEHQ-P26_1hr_24th July26.png | 111x221 | y | 0.0 | 0.0 | 1.71 | 0.00/0.00/0.00/0.00 | 26.2 | photometry_ok | - |
| MEHQ-P27-4hr_28th July26.png | 119x160 | y | 0.0 | 0.0 | 2.56 | 0.00/0.87/0.00/0.39 | 29.5 | photometry_ok | - |
| MEHQ-P28-4hr_28th July26.png | 91x201 | y | 0.0297 | 0.143 | 0.51 | 0.00/0.00/0.00/0.00 | 20.5 | photometry_ok | - |
| MEHQ-P29-4hr_29th July26 (1).png | 71x130 | y | 0.5287 | 0.6685 | 1.49 | 0.00/0.00/0.00/0.00 | 17.2 | positions_only | - |
| MEHQ-P29-4hr_29th July26.png | 71x130 | y | 0.5287 | 0.6685 | 1.49 | 0.00/0.00/0.00/0.00 | 17.2 | positions_only | MEHQ-P29-4hr_29th July26 (1).png |
| MEHQ-P30-4hr_29th July26.png | 100x170 | y | 0.1811 | 0.4098 | 0.48 | 0.00/0.28/0.00/0.00 | 23.5 | positions_only | - |
| MEHQ-P31-4hr_30th July26.png | 121x197 | y | 0.1397 | 0.3763 | 3.23 | 0.00/0.00/0.00/0.00 | 28.8 | photometry_partial | - |
| MEHQ-P32-1_3hr_4th Aug26_ACN (1).png | 77x125 | y | 0.108 | 0.229 | 2.43 | 0.00/0.00/0.00/0.00 | 17.5 | photometry_partial | - |
| MEHQ-P32-1_3hr_4th Aug26_ACN (2).png | 77x125 | y | 0.108 | 0.229 | 2.43 | 0.00/0.00/0.00/0.00 | 17.5 | photometry_partial | MEHQ-P32-1_3hr_4th Aug26_ACN (1).png |
| MEHQ-P32-1_3hr_4th Aug26_ACN.png | 77x125 | y | 0.108 | 0.229 | 2.43 | 0.00/0.00/0.00/0.00 | 17.5 | photometry_partial | MEHQ-P32-1_3hr_4th Aug26_ACN (1).png |
| MEHQ-P32-4hr_30th July26 (1).png | 93x166 | y | 0.2974 | 0.5344 | 3.2 | 0.00/0.40/0.03/0.04 | 23.2 | positions_only | - |
| MEHQ-P32-4hr_30th July26.png | 93x166 | y | 0.2974 | 0.5344 | 3.2 | 0.00/0.40/0.03/0.04 | 23.2 | positions_only | MEHQ-P32-4hr_30th July26 (1).png |
| MEHQ-P32_4+3hr_3rd Aug26 (1).png | 112x184 | y | 0.1664 | 0.4164 | 1.39 | 0.00/0.00/0.00/0.00 | 27.2 | positions_only | - |
| MEHQ-P32_4+3hr_3rd Aug26.png | 112x184 | y | 0.1664 | 0.4164 | 1.39 | 0.00/0.00/0.00/0.00 | 27.2 | positions_only | MEHQ-P32_4+3hr_3rd Aug26 (1).png |
| MEHQ-P33 4hr_31st July26.png | 158x299 | y | 0.0 | 0.0 | 0.12 | 0.00/0.00/0.00/0.00 | 37.8 | photometry_ok | - |
| MEHQ-P34-1_3hr_4th Aug26_ACN.png | 118x188 | y | 0.0416 | 0.1523 | 2.94 | 0.00/0.00/0.00/0.00 | 27.0 | photometry_ok | - |
| MEHQ-P34_4hr_3rd Aug26.png | 154x208 | y | 0.0 | 0.0068 | 0.19 | 0.03/0.64/0.00/0.98 | 38.0 | photometry_ok | - |
| MEHQ-P35_4hr_4th Aug26.png | 101x166 | y | 0.207 | 0.4826 | 2.44 | 0.61/0.90/0.00/0.00 | 23.2 | positions_only | - |
| MEHQ-P35_6hr_4th Aug26.png | 114x226 | y | 0.0725 | 0.1976 | 1.97 | 0.00/0.00/0.22/0.24 | 28.5 | photometry_partial | - |
| MEHQ-P36_4hr_4th Aug26.png | 97x180 | y | 0.166 | 0.3433 | 1.02 | 0.00/0.97/0.00/0.89 | 23.5 | positions_only | - |
| MEHQ-P36_6hr_4th Aug26.png | 125x216 | y | 0.0715 | 0.1911 | 1.45 | 0.00/0.08/0.00/0.00 | 29.0 | photometry_partial | - |
| MEHQ-P37-4hr_6th Aug26.png | 119x207 | y | 0.0008 | 0.0652 | 4.09 | 0.00/0.72/0.18/0.14 | 29.8 | photometry_ok | - |
| MEHQ-P37-7hr_6th Aug26.png | 163x270 | y | 0.0 | 0.0 | 2.72 | 0.00/0.00/0.01/0.15 | 40.8 | photometry_ok | - |
| MEHQ-P38-1st Step_3hr_6th Aug26.png | 99x190 | y | 0.0069 | 0.0481 | 0.24 | 0.00/0.00/0.00/0.00 | 21.8 | photometry_ok | - |
| MEHQ-P38-1st Step_6hr_6th Aug26.png | 153x293 | y | 0.0 | 0.0 | 1.65 | 0.00/0.00/0.00/0.34 | 36.0 | photometry_ok | - |
| MEHQ-P39_3hr_6th Aug26.png | 91x203 | y | 0.1383 | 0.306 | 0.61 | 0.00/0.00/0.00/0.00 | 21.8 | photometry_partial | - |
| MEHQ-P40_3hr_6th Aug26.png | 102x208 | y | 0.0309 | 0.1663 | 0.28 | 0.00/0.00/0.00/0.00 | 23.2 | photometry_ok | - |
| MEHQ-P41 Step 1-6hr_10th Aug26.png | 113x210 | y | 0.0133 | 0.1834 | 3.59 | 0.00/0.62/0.18/0.00 | 27.8 | photometry_ok | - |
| MEHQ-P42 Step 1-6hr_10th Aug26.png | 108x205 | y | 0.004 | 0.1111 | 2.08 | 0.00/0.00/0.00/0.00 | 26.2 | photometry_ok | - |
| MEHQ-P43 Step 1 6hr_10th Aug26.png | 133x231 | y | 0.0 | 0.0036 | 1.89 | 0.00/0.00/0.00/0.00 | 32.2 | photometry_ok | - |
| MEHQ-P44 Step 1 6hr_10th Aug26.png | 140x288 | y | 0.0001 | 0.0516 | 0.71 | 0.11/0.00/0.00/0.91 | 33.8 | photometry_ok | - |
| MEHQ-P45 Step 1_4hr_11th Aug26.png | 96x195 | y | 0.1197 | 0.3993 | 0.12 | 0.00/0.00/0.00/0.00 | 22.2 | photometry_partial | - |
| MEHQ-P46 Step 1_4hr_11th Aug26.png | 115x209 | y | 0.0 | 0.0584 | 3.89 | 0.00/0.23/0.35/0.00 | 28.2 | photometry_ok | - |
| PER-P19-Opt 1-2hr.png | 118x192 | y | 0.0007 | 0.1384 | 0.05 | 0.00/0.00/0.00/0.00 | 24.8 | photometry_ok | - |
| PER-P19-Opt 1-4hr.png | 109x167 | y | 0.08 | 0.5125 | 2.74 | 0.00/0.00/0.00/0.00 | 22.8 | photometry_partial | - |
| PER-P19-Opt 1-6hr.png | 104x180 | y | 0.0243 | 0.131 | 1.55 | 0.00/0.00/0.00/0.00 | 24.2 | photometry_ok | - |
| PER-P19-Opt 2-2hr.png | 100x182 | y | 0.0826 | 0.2571 | 2.43 | 0.00/0.00/0.00/0.31 | 24.5 | photometry_partial | - |
| PER-P19-Opt 2-4hr.png | 111x190 | y | 0.1436 | 0.2301 | 1.64 | 0.00/0.97/0.07/0.00 | 27.5 | photometry_partial | - |
| PER-P19-Opt 2-6hr.png | 119x220 | y | 0.0115 | 0.1253 | 0.56 | 0.00/0.10/0.00/0.00 | 27.5 | photometry_ok | - |
| PER-P19-Opt 3-2hr_18th July26.png | 107x205 | y | 0.009 | 0.0806 | 1.39 | 0.00/0.00/0.23/0.66 | 26.8 | photometry_ok | - |
| PER-P19-Opt 3-4hr_18th July26.png | 126x221 | y | 0.0 | 0.0 | 1.79 | 0.00/0.96/0.00/0.00 | 30.5 | photometry_ok | - |
| PER-P19-Opt 3-6hr_18th July26.png | 204x343 | y | 0.0 | 0.0 | 2.06 | 0.02/0.00/0.00/0.00 | 47.5 | photometry_ok | - |
| PER-P19-Opt 3-Hexane_22nd July26.png | 96x169 | y | 0.0829 | 0.2251 | 1.04 | 0.00/0.00/0.27/0.60 | 24.0 | photometry_partial | - |
| PER-P19-Opt 3-Methanol_22nd July26.png | 113x200 | y | 0.0053 | 0.12 | 0.51 | 0.00/0.00/0.57/0.01 | 28.2 | photometry_ok | - |
| PER-P19-Opt 4-2hr_18th July26.png | 129x213 | y | 0.0 | 0.0 | 2.2 | 0.01/0.25/0.00/0.00 | 28.5 | photometry_ok | - |
| PER-P19-Opt 4-4hr_18th July26.png | 112x237 | y | 0.0073 | 0.1004 | 1.47 | 0.00/0.00/0.10/0.18 | 28.0 | photometry_ok | - |
| PER-P19-Opt 4-6hr_18th July26.png | 175x289 | y | 0.0 | 0.0 | 1.86 | 0.00/0.00/0.16/0.00 | 42.8 | photometry_ok | - |
| PER-P19-Opt 4-Hexane_22nd July26.png | 132x221 | y | 0.1007 | 0.1934 | 1.54 | 0.00/0.20/0.00/0.00 | 31.2 | photometry_partial | - |
| PER-P19-Opt 4-Methanol_22nd July26.png | 136x224 | y | 0.0 | 0.0029 | 1.73 | 0.16/0.34/0.00/0.00 | 33.2 | photometry_ok | - |
| PER-P19-Opt 5-4hr MeOH_21st July26 .png | 104x151 | y | 0.0063 | 0.1616 | 0.15 | 0.00/0.00/0.00/0.00 | 24.0 | photometry_ok | - |
| PER-P19-Opt 5-6hr MeOH_21st July26.png | 89x140 | y | 0.4042 | 0.6333 | 2.34 | 0.00/0.00/0.31/0.48 | 22.2 | positions_only | - |
| Scale-up_MEHQ-P20-Step-1_3.5hr_30th July26.png | 126x231 | y | 0.0074 | 0.1326 | 2.09 | 0.00/0.00/0.00/0.00 | 30.8 | photometry_ok | - |

## Capture-protocol observations

- Images are extremely low resolution (71-158 px wide). At ~15-40 px/lane this sits at or
  below the 10 px/lane stability floor from F13 for faint-band sensitivity, and far below
  any OCR floor (F8). These appear to be downsampled exports, not native photographs.
- The remedy is a capture-protocol change (native-resolution export, controlled exposure to
  keep green-channel clipping < 5%, full plate in frame with margin, pencil solvent front),
  not more code. See reports/CAPTURE_PROTOCOL.md.
