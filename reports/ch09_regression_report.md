# Chapter 09 Regression Report

Selected model (from train CV only): Random Forest
Final-test rows: 56

## Final comparison

```text
        model       selection_role     train_mae      test_mae     test_rmse   test_r2  mae_improvement_vs_baseline_pct
Baseline Mean             baseline 416266.191884 443449.648712 522189.699620 -0.001121                             0.00
Random Forest selected_by_train_cv 225894.200725 495864.885326 569959.231662 -0.192662                           -11.82
```

## Validation

```text
                                    check         value        expected status
                forbidden_feature_overlap             0               0   PASS
                 strict_train_before_test          True            True   PASS
        selected_model_exists_in_train_cv Random Forest candidate model   PASS
             final_test_contains_baseline          True            True   PASS
final_test_contains_frozen_selected_model          True            True   PASS
                         test_rows_for_r2            56             >=2   PASS
```
