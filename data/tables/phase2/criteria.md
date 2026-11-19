| metric                                         | threshold                       |   value | ci             | pass   |
|:-----------------------------------------------|:--------------------------------|--------:|:---------------|:-------|
| invalidation recall                            | >= 0.90                         |   0.873 | [0.845, 0.906] | False  |
| observed false-reuse rate (probed dependent)   | upper CI <= alpha + 0.05 = 0.10 |   0.215 | [0.159, 0.276] | False  |
| reuse precision                                | >= 0.95                         |   0.783 |                | False  |
| utility R4p - R1 (accidental), non-inferiority | lower 95% bound > -0.1          |   0.067 | lower -0.062   | True   |
| recovery tokens / R1                           | <= 0.5                          |   0.650 |                | False  |
| branches with duplicated external effects      | 0                               |   0.000 |                | True   |