from app.querying.column_labels import column_labels


def test_jobs_market_column_labels_cover_raw_and_aggregate_columns() -> None:
    labels = column_labels([
        "job_id", "title", "company", "city", "salary_raw",
        "salary_min_k", "salary_max_k", "experience", "degree",
        "skills", "welfare", "avg(salary_min_k)", "avg_salary_min_k", "count(*)", "count_job_id",
    ])

    assert labels["job_id"] == "岗位编号"
    assert labels["salary_min_k"] == "月薪下限K"
    assert labels["avg(salary_min_k)"] == "平均月薪下限K"
    assert labels["avg_salary_min_k"] == "平均月薪下限K"
    assert labels["count(*)"] == "岗位数量"
    assert labels["count_job_id"] == "岗位数量"
