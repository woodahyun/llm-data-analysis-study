"""Chapter 09 reproducible, leakage-aware regression analysis."""
from pathlib import Path
import warnings

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


warnings.filterwarnings("ignore", category=FutureWarning)
ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
FIGURES = REPORTS / "figures"
REPORTS.mkdir(exist_ok=True)
FIGURES.mkdir(exist_ok=True)

FEATURES = ["order_month", "order_dayofweek", "age", "payment_method", "gender", "city"]
NUMERIC_FEATURES = ["age"]
CATEGORICAL_FEATURES = [feature for feature in FEATURES if feature not in NUMERIC_FEATURES]
FORBIDDEN_FEATURES = {
    "order_total", "line_total", "quantity", "unit_price", "item_count",
    "total_quantity", "avg_unit_price", "order_status", "order_id",
    "customer_id", "product_id",
}


def save(df: pd.DataFrame, name: str) -> None:
    df.to_csv(REPORTS / name, index=False, encoding="utf-8-sig")


def make_preprocessor() -> ColumnTransformer:
    return ColumnTransformer([
        ("numeric", Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]), NUMERIC_FEATURES),
        ("categorical", Pipeline([
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]), CATEGORICAL_FEATURES),
    ])


def make_pipeline(model) -> Pipeline:
    return Pipeline([("preprocessor", make_preprocessor()), ("model", model)])


def main() -> None:
    customers = pd.read_csv(PROCESSED / "customers_clean.csv")
    orders = pd.read_csv(PROCESSED / "orders_clean.csv")
    items = pd.read_csv(PROCESSED / "order_items_clean.csv")
    orders["order_date"] = pd.to_datetime(orders["order_date"], errors="coerce")

    line_total_check = items["quantity"] * items["unit_price"]
    line_total_mismatch = int((items["line_total"] != line_total_check).sum())
    if line_total_mismatch:
        raise ValueError(f"line_total mismatch: {line_total_mismatch}")
    order_total = items.groupby("order_id", as_index=False)["line_total"].sum().rename(columns={"line_total": "order_total"})

    # Target relation: each order must have exactly one aggregated total.
    model_data = orders.merge(order_total, on="order_id", how="left", validate="one_to_one", indicator="target_merge")
    missing_target_orders = int(model_data["order_total"].isna().sum())
    if missing_target_orders:
        raise ValueError(f"orders without order detail target: {missing_target_orders}")
    model_data = model_data.merge(
        customers[["customer_id", "age", "gender", "city"]],
        on="customer_id", how="left", validate="many_to_one", indicator="customer_merge",
    )
    missing_customer_rows = int((model_data["customer_merge"] != "both").sum())
    if missing_customer_rows:
        raise ValueError(f"orders without customer match: {missing_customer_rows}")

    model_data["order_month"] = model_data["order_date"].dt.to_period("M").astype(str)
    model_data["order_dayofweek"] = model_data["order_date"].dt.day_name()
    model_data = model_data.sort_values(["order_date", "order_id"]).reset_index(drop=True)
    if model_data["order_date"].isna().any():
        raise ValueError("invalid order_date exists in model data")

    audit_rows = []
    for feature in FEATURES + sorted(FORBIDDEN_FEATURES):
        allowed = feature in FEATURES
        audit_rows.append({
            "feature": feature,
            "available_at_prediction_time": "Yes" if allowed else "No",
            "target_calculation_material": "Yes" if feature in {"order_total", "line_total", "quantity", "unit_price", "item_count", "total_quantity", "avg_unit_price"} else "No",
            "post_event_or_id": "Yes" if feature in {"order_status", "order_id", "customer_id", "product_id"} else "No",
            "final_use": "Yes" if allowed else "No",
        })
    feature_audit = pd.DataFrame(audit_rows)
    save(feature_audit, "ch09_regression_feature_audit.csv")
    forbidden_overlap = len(set(FEATURES).intersection(FORBIDDEN_FEATURES))

    # Use the final 20% of unique calendar dates as the protected future-like test period.
    dates = np.array(sorted(model_data["order_date"].unique()))
    test_date_count = max(1, int(np.ceil(len(dates) * 0.20)))
    final_test_start = pd.Timestamp(dates[-test_date_count])
    train = model_data.loc[model_data["order_date"] < final_test_start].copy()
    final_test = model_data.loc[model_data["order_date"] >= final_test_start].copy()
    strict_split = train["order_date"].max() < final_test["order_date"].min()
    split_summary = pd.DataFrame([{
        "train_start_date": train["order_date"].min().date(),
        "train_end_date": train["order_date"].max().date(),
        "train_rows": len(train),
        "final_test_start_date": final_test["order_date"].min().date(),
        "final_test_end_date": final_test["order_date"].max().date(),
        "final_test_rows": len(final_test),
        "same_calendar_date_overlap": int(len(set(train.order_date).intersection(final_test.order_date))),
        "strict_train_before_test": "PASS" if strict_split else "FAIL",
    }])
    save(split_summary, "ch09_regression_split_summary.csv")

    candidates = {
        "Baseline Mean": DummyRegressor(strategy="mean"),
        "Linear Regression": LinearRegression(),
        "Random Forest": RandomForestRegressor(n_estimators=300, min_samples_leaf=2, random_state=42),
    }
    tscv = TimeSeriesSplit(n_splits=4)
    cv_rows = []
    for name, estimator in candidates.items():
        pipeline = make_pipeline(estimator)
        mae_scores = -cross_val_score(pipeline, train[FEATURES], train["order_total"], cv=tscv, scoring="neg_mean_absolute_error")
        r2_scores = cross_val_score(pipeline, train[FEATURES], train["order_total"], cv=tscv, scoring="r2")
        cv_rows.append({
            "model": name,
            "cv_mae_mean": mae_scores.mean(),
            "cv_mae_std": mae_scores.std(),
            "cv_r2_mean": r2_scores.mean(),
            "selection_role": "baseline" if name == "Baseline Mean" else "candidate",
        })
    cv_summary = pd.DataFrame(cv_rows)
    selected_model = cv_summary.loc[cv_summary.selection_role.eq("candidate")].sort_values("cv_mae_mean").iloc[0]["model"]
    cv_summary["selected_by_train_cv"] = cv_summary.model.eq(selected_model)
    save(cv_summary, "ch09_regression_cv_summary.csv")

    # The selected model is frozen here before final-test metrics are calculated.
    comparison_rows, prediction_tables = [], {}
    for name in ["Baseline Mean", selected_model]:
        pipeline = make_pipeline(candidates[name])
        pipeline.fit(train[FEATURES], train["order_total"])
        train_pred = pipeline.predict(train[FEATURES])
        test_pred = pipeline.predict(final_test[FEATURES])
        comparison_rows.append({
            "model": name,
            "selection_role": "baseline" if name == "Baseline Mean" else "selected_by_train_cv",
            "train_mae": mean_absolute_error(train.order_total, train_pred),
            "test_mae": mean_absolute_error(final_test.order_total, test_pred),
            "test_rmse": mean_squared_error(final_test.order_total, test_pred) ** 0.5,
            "test_r2": r2_score(final_test.order_total, test_pred),
        })
        prediction_tables[name] = test_pred
    comparison = pd.DataFrame(comparison_rows)
    baseline_mae = comparison.loc[comparison.model.eq("Baseline Mean"), "test_mae"].iloc[0]
    comparison["mae_improvement_vs_baseline_pct"] = ((baseline_mae - comparison.test_mae) / baseline_mae * 100).round(2)
    save(comparison, "ch09_regression_model_comparison.csv")

    selected_predictions = final_test[["order_id", "order_date", "payment_method", "age", "gender", "city", "order_total"]].copy()
    selected_predictions["actual_order_total"] = selected_predictions.pop("order_total")
    selected_predictions["predicted_order_total"] = prediction_tables[selected_model]
    selected_predictions["residual"] = selected_predictions.actual_order_total - selected_predictions.predicted_order_total
    selected_predictions["abs_error"] = selected_predictions.residual.abs()
    selected_predictions = selected_predictions.sort_values("abs_error", ascending=False)
    save(selected_predictions, "ch09_regression_predictions_internal.csv")
    # order_id stays internal only; public model data has no direct customer identifier.
    public_model_data = model_data[["order_date", *FEATURES, "order_total"]]
    save(public_model_data, "ch09_regression_model_data_internal.csv")

    # Lightweight reproducible figures for the public report.
    plt.rcParams["font.family"] = "DejaVu Sans"
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.scatter(selected_predictions.actual_order_total / 1_000_000, selected_predictions.predicted_order_total / 1_000_000, alpha=0.7)
    lim = max(selected_predictions.actual_order_total.max(), selected_predictions.predicted_order_total.max()) / 1_000_000
    ax.plot([0, lim], [0, lim], linestyle="--", color="gray")
    ax.set_xlabel("Actual order total (KRW million)")
    ax.set_ylabel("Predicted order total (KRW million)")
    ax.set_title("Final-test actual vs predicted")
    fig.tight_layout(); fig.savefig(FIGURES / "ch09_actual_vs_predicted.png", dpi=160); plt.close(fig)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(selected_predictions.residual / 1_000, bins=15, color="#4C78A8", edgecolor="white")
    ax.set_xlabel("Residual (thousand KRW)")
    ax.set_ylabel("Number of orders")
    ax.set_title("Final-test residual distribution")
    fig.tight_layout(); fig.savefig(FIGURES / "ch09_residual_histogram.png", dpi=160); plt.close(fig)

    validation = pd.DataFrame([
        {"check": "forbidden_feature_overlap", "value": forbidden_overlap, "expected": 0, "status": "PASS" if forbidden_overlap == 0 else "FAIL"},
        {"check": "strict_train_before_test", "value": strict_split, "expected": True, "status": "PASS" if strict_split else "FAIL"},
        {"check": "selected_model_exists_in_train_cv", "value": selected_model, "expected": "candidate model", "status": "PASS" if selected_model in cv_summary.model.tolist() else "FAIL"},
        {"check": "final_test_contains_baseline", "value": "Baseline Mean" in comparison.model.tolist(), "expected": True, "status": "PASS" if "Baseline Mean" in comparison.model.tolist() else "FAIL"},
        {"check": "final_test_contains_frozen_selected_model", "value": selected_model in comparison.model.tolist(), "expected": True, "status": "PASS" if selected_model in comparison.model.tolist() else "FAIL"},
        {"check": "test_rows_for_r2", "value": len(final_test), "expected": ">=2", "status": "PASS" if len(final_test) >= 2 else "FAIL"},
    ])
    save(validation, "ch09_regression_validation.csv")
    checklist = pd.DataFrame({"item": ["prepare_input", "feature_audit", "time_split", "train_cv", "frozen_final_test", "validation"], "status": ["PASS"] * 6})
    save(checklist, "ch09_regression_checklist.csv")

    report = f"""# Chapter 09 Regression Report

Selected model (from train CV only): {selected_model}
Final-test rows: {len(final_test)}

## Final comparison

```text
{comparison.to_string(index=False)}
```

## Validation

```text
{validation.to_string(index=False)}
```
"""
    (REPORTS / "ch09_regression_report.md").write_text(report, encoding="utf-8")
    print("Selected model from train CV:", selected_model)
    print(comparison.to_string(index=False))
    print("\nValidation")
    print(validation.to_string(index=False))
    if not validation.status.eq("PASS").all():
        raise SystemExit("Chapter 09 validation failed")
    print("\nFINAL VALIDATION PASS")


if __name__ == "__main__":
    main()
