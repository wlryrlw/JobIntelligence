from app.demo_data import seed_demo_data


if __name__ == "__main__":
    counts = seed_demo_data()
    print("测试数据已准备：")
    for table, count in counts.items():
        print(f"- {table}: {count} 行")
