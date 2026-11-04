| family      | graph          |   n_artifacts |   dependent_share |   precision |   recall |   f1 |   tp |   fp |   fn |   f1_lo |   f1_hi | model    |
|:------------|:---------------|--------------:|------------------:|------------:|---------:|-----:|-----:|-----:|-----:|--------:|--------:|:---------|
| accidental  | declared       |          3389 |              0.66 |        0.78 |     0.65 | 0.71 | 1449 |  413 |  780 |    0.59 |    0.78 | Qwen3-8B |
| accidental  | dataflow       |          3389 |              0.66 |        0.66 |     1.00 | 0.79 | 2229 | 1160 |    0 |    0.72 |    0.84 | Qwen3-8B |
| accidental  | taint          |          3389 |              0.66 |        0.98 |     0.62 | 0.76 | 1381 |   30 |  848 |    0.67 |    0.82 | Qwen3-8B |
| accidental  | declared_taint |          3389 |              0.66 |        0.79 |     0.73 | 0.76 | 1617 |  432 |  612 |    0.67 |    0.82 | Qwen3-8B |
| adversarial | declared       |          2976 |              0.52 |        0.75 |     0.57 | 0.65 |  889 |  297 |  657 |    0.57 |    0.73 | Qwen3-8B |
| adversarial | dataflow       |          2976 |              0.52 |        0.52 |     1.00 | 0.68 | 1546 | 1430 |    0 |    0.62 |    0.76 | Qwen3-8B |
| adversarial | taint          |          2976 |              0.52 |        0.99 |     0.55 | 0.71 |  852 |    8 |  694 |    0.64 |    0.78 | Qwen3-8B |
| adversarial | declared_taint |          2976 |              0.52 |        0.78 |     0.72 | 0.75 | 1110 |  305 |  436 |    0.68 |    0.81 | Qwen3-8B |
| accidental  | declared       |          3382 |              0.74 |        0.79 |     0.49 | 0.60 | 1214 |  331 | 1278 |    0.47 |    0.71 | Qwen3-4B |
| accidental  | dataflow       |          3382 |              0.74 |        0.74 |     1.00 | 0.85 | 2492 |  890 |    0 |    0.76 |    0.90 | Qwen3-4B |
| accidental  | taint          |          3382 |              0.74 |        1.00 |     0.55 | 0.71 | 1373 |    0 | 1119 |    0.61 |    0.78 | Qwen3-4B |
| accidental  | declared_taint |          3382 |              0.74 |        0.83 |     0.65 | 0.73 | 1623 |  331 |  869 |    0.61 |    0.78 | Qwen3-4B |
| adversarial | declared       |          3692 |              0.55 |        0.82 |     0.43 | 0.57 |  874 |  196 | 1148 |    0.48 |    0.64 | Qwen3-4B |
| adversarial | dataflow       |          3692 |              0.55 |        0.55 |     1.00 | 0.71 | 2022 | 1670 |    0 |    0.65 |    0.77 | Qwen3-4B |
| adversarial | taint          |          3692 |              0.55 |        1.00 |     0.54 | 0.70 | 1088 |    0 |  934 |    0.59 |    0.79 | Qwen3-4B |
| adversarial | declared_taint |          3692 |              0.55 |        0.86 |     0.62 | 0.72 | 1253 |  196 |  769 |    0.64 |    0.79 | Qwen3-4B |