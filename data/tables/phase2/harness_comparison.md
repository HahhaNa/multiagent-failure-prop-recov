| metric                            | main                         | main_h2              |
|:----------------------------------|:-----------------------------|:---------------------|
| harness                           | phase 1 (empty tool message) | fixed (error text)   |
| refs                              | 140                          | 140                  |
| refs_with_disallowed              | 106                          | 106                  |
| disallowed_calls                  | 208                          | 356                  |
| clean_success_message             | 0.8571                       | 0.8286               |
| clean_success_state               | 0.7857                       | 0.6                  |
| delta_accidental_message          | 0.409 [0.265, 0.553]         | 0.377 [0.231, 0.535] |
| delta_accidental_state            | 0.425 [0.236, 0.598]         | 0.294 [0.119, 0.442] |
| delta_adversarial_message         | 0.678 [0.585, 0.763]         | 0.603 [0.473, 0.716] |
| delta_adversarial_state           | 0.484 [0.312, 0.617]         | 0.380 [0.205, 0.549] |
| recall_taint_accidental           | 0.6196                       | 0.602                |
| recall_declared_taint_accidental  | 0.7254                       | 0.7031               |
| recall_taint_adversarial          | 0.5511                       | 0.5284               |
| recall_declared_taint_adversarial | 0.718                        | 0.6707               |
| recall_taint_control              | 0.3086                       | 0.1774               |
| recall_declared_taint_control     | 0.8272                       | 0.5806               |