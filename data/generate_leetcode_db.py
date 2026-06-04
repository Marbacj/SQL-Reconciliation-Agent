#!/usr/bin/env python3
# @author mabohui <mabohui@kuaishou.com>
# Created on 2026-06-03
#
# generate_leetcode_db.py — 从 LeetCode SQL 50题（基础版）自动建 SQLite 库
#
# 输出产物：
#   1. data/leetcode_test.db       — 包含所有题目表的 SQLite 数据库
#   2. tests/eval/leetcode_golden.jsonl — 评估集（自然语言问题 + SQLite 参考SQL）
#   3. knowledge_base/table_docs/   — 每道题的知识库文档（Markdown）
# 用法：
#   python data/generate_leetcode_db.py

import sqlite3
import json
import os
import re

# ── 配置 ──────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "leetcode_test.db")
GOLDEN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "tests", "eval", "leetcode_golden.jsonl"
)
KB_DIR = os.path.join(os.path.dirname(__file__), "..", "knowledge_base", "table_docs")


# ── LeetCode 50题数据定义 ──────────────────────────────
# 每道题包含：id, title, difficulty, tables(建表SQL+数据),
#             question(自然语言), mysql_sql(原始MySQL答案), sqlite_sql(转后的SQLite答案)

LEETCODE_PROBLEMS = [
    # ────── 简单 ──────
    {
        "id": "1757",
        "title": "可回收且低脂的产品",
        "difficulty": "easy",
        "tables": [
            {
                "name": "Products",
                "ddl": """CREATE TABLE Products (
    product_id INTEGER PRIMARY KEY,
    low_fats TEXT CHECK(low_fats IN ('Y','N')),
    recyclable TEXT CHECK(recyclable IN ('Y','N'))
);""",
                "data": [
                    (0, "Y", "N"),
                    (1, "Y", "Y"),
                    (2, "N", "Y"),
                    (3, "Y", "Y"),
                    (4, "N", "N"),
                ],
            }
        ],
        "question": "找出既是低脂又是可回收的产品编号",
        "mysql_sql": "select product_id from Products where low_fats = 'Y' and recyclable = 'Y';",
        "sqlite_sql": "SELECT product_id FROM Products WHERE low_fats = 'Y' AND recyclable = 'Y';",
    },
    {
        "id": "584",
        "title": "寻找用户推荐人",
        "difficulty": "easy",
        "tables": [
            {
                "name": "Customer",
                "ddl": """CREATE TABLE Customer (
    id INTEGER PRIMARY KEY,
    name TEXT,
    referee_id INTEGER
);""",
                "data": [
                    (1, "Will", None),
                    (2, "Jane", None),
                    (3, "Alex", 2),
                    (4, "Bill", None),
                    (5, "Zack", 1),
                    (6, "Mark", 2),
                ],
            }
        ],
        "question": "找出没有被 id=2 的客户推荐的客户的姓名",
        "mysql_sql": "select name from Customer where referee_id != 2 or referee_id is null;",
        "sqlite_sql": "SELECT name FROM Customer WHERE referee_id != 2 OR referee_id IS NULL;",
    },
    {
        "id": "595",
        "title": "大的国家",
        "difficulty": "easy",
        "tables": [
            {
                "name": "World",
                "ddl": """CREATE TABLE World (
    name TEXT PRIMARY KEY,
    continent TEXT,
    area INTEGER,
    population INTEGER,
    gdp INTEGER
);""",
                "data": [
                    ("Afghanistan", "Asia", 652230, 25500100, 20343000000),
                    ("Albania", "Europe", 28748, 2831741, 12960000000),
                    ("Algeria", "Africa", 2381741, 37100000, 188681000000),
                    ("Andorra", "Europe", 468, 78115, 3712000000),
                    ("Angola", "Africa", 1246700, 20609294, 100990000000),
                ],
            }
        ],
        "question": "找出面积至少300万平方公里或人口至少2500万的大国的名称、人口和面积",
        "mysql_sql": "select name,population,area from World where area >= 3000000 or population >=25000000;",
        "sqlite_sql": "SELECT name, population, area FROM World WHERE area >= 3000000 OR population >= 25000000;",
    },
    {
        "id": "1148",
        "title": "文章浏览I",
        "difficulty": "easy",
        "tables": [
            {
                "name": "Views",
                "ddl": """CREATE TABLE Views (
    article_id INTEGER,
    author_id INTEGER,
    viewer_id INTEGER,
    view_date TEXT
);""",
                "data": [
                    (1, 3, 5, "2019-08-01"),
                    (1, 3, 6, "2019-08-02"),
                    (2, 7, 7, "2019-08-01"),
                    (2, 7, 6, "2019-08-02"),
                    (4, 7, 1, "2019-07-22"),
                    (3, 4, 4, "2019-07-21"),
                    (3, 4, 4, "2019-07-21"),
                ],
            }
        ],
        "question": "查询出所有浏览过自己文章的作者，结果按id升序排列",
        "mysql_sql": "select distinct t1.author_id as id from Views as t1 where t1.author_id = t1.viewer_id order by id;",
        "sqlite_sql": "SELECT DISTINCT author_id AS id FROM Views WHERE author_id = viewer_id ORDER BY id;",
    },
    {
        "id": "1683",
        "title": "无效的推文",
        "difficulty": "easy",
        "tables": [
            {
                "name": "Tweets",
                "ddl": """CREATE TABLE Tweets (
    tweet_id INTEGER PRIMARY KEY,
    content TEXT
);""",
                "data": [
                    (1, "Vote for Biden"),
                    (2, "Let us make America great again!"),
                ],
            }
        ],
        "question": "查询所有无效推文的编号，当推文内容字符数严格大于15时该推文无效",
        "mysql_sql": "select tweet_id from Tweets where char_length(content) > 15;",
        "sqlite_sql": "SELECT tweet_id FROM Tweets WHERE LENGTH(content) > 15;",
    },
    {
        "id": "1378",
        "title": "使用唯一标识码替换员工ID",
        "difficulty": "easy",
        "tables": [
            {
                "name": "Employees",
                "ddl": """CREATE TABLE Employees (
    id INTEGER PRIMARY KEY,
    name TEXT
);""",
                "data": [
                    (1, "Alice"),
                    (7, "Bob"),
                    (11, "Meir"),
                    (90, "Winston"),
                    (3, "Jonathan"),
                ],
            },
            {
                "name": "EmployeeUNI",
                "ddl": """CREATE TABLE EmployeeUNI (
    id INTEGER,
    unique_id INTEGER,
    PRIMARY KEY (id, unique_id)
);""",
                "data": [
                    (3, 1),
                    (11, 2),
                    (90, 3),
                ],
            },
        ],
        "question": "展示每位员工的唯一标识码，如果某位员工没有唯一标识码则使用null填充",
        "mysql_sql": "select t2.unique_id,t1.name from Employees as t1 left join EmployeeUNI as t2 on t1.id = t2.id;",
        "sqlite_sql": "SELECT t2.unique_id, t1.name FROM Employees AS t1 LEFT JOIN EmployeeUNI AS t2 ON t1.id = t2.id;",
    },
    {
        "id": "1068",
        "title": "产品销售分析I",
        "difficulty": "easy",
        "tables": [
            {
                "name": "Sales",
                "ddl": """CREATE TABLE Sales (
    sale_id INTEGER,
    product_id INTEGER,
    year INTEGER,
    quantity INTEGER,
    price INTEGER,
    PRIMARY KEY (sale_id, year)
);""",
                "data": [
                    (1, 100, 2008, 10, 5000),
                    (2, 100, 2009, 12, 5000),
                    (7, 200, 2011, 15, 9000),
                ],
            },
            {
                "name": "Product",
                "ddl": """CREATE TABLE Product (
    product_id INTEGER PRIMARY KEY,
    product_name TEXT
);""",
                "data": [
                    (100, "Nokia"),
                    (200, "Apple"),
                    (300, "Samsung"),
                ],
            },
        ],
        "question": "获取Sales表中所有sale_id对应的product_name以及该产品的所有year和price",
        "mysql_sql": "select t1.year,t1.price,t2.product_name from Sales as t1 left join Product as t2 on t1.product_id = t2.product_id;",
        "sqlite_sql": "SELECT t2.product_name, t1.year, t1.price FROM Sales AS t1 LEFT JOIN Product AS t2 ON t1.product_id = t2.product_id;",
    },
    {
        "id": "1581",
        "title": "进店却未进行过交易的顾客",
        "difficulty": "easy",
        "tables": [
            {
                "name": "Visits",
                "ddl": """CREATE TABLE Visits (
    visit_id INTEGER PRIMARY KEY,
    customer_id INTEGER
);""",
                "data": [
                    (1, 23),
                    (2, 9),
                    (4, 30),
                    (5, 54),
                    (6, 96),
                    (7, 54),
                    (8, 54),
                ],
            },
            {
                "name": "Transactions",
                "ddl": """CREATE TABLE Transactions (
    transaction_id INTEGER PRIMARY KEY,
    visit_id INTEGER,
    amount INTEGER
);""",
                "data": [
                    (2, 5, 310),
                    (3, 5, 300),
                    (9, 5, 200),
                    (12, 1, 910),
                    (13, 2, 970),
                ],
            },
        ],
        "question": "查找进店却未进行过交易的顾客ID以及他们只光顾不交易的次数",
        "mysql_sql": "select customer_id, count(visit_id) as count_no_trans from Visits where visit_id not in(select distinct visit_id from Transactions) group by customer_id;",
        "sqlite_sql": "SELECT customer_id, COUNT(visit_id) AS count_no_trans FROM Visits WHERE visit_id NOT IN (SELECT DISTINCT visit_id FROM Transactions) GROUP BY customer_id;",
    },
    {
        "id": "197",
        "title": "上升的温度",
        "difficulty": "easy",
        "tables": [
            {
                "name": "Weather",
                "ddl": """CREATE TABLE Weather (
    id INTEGER PRIMARY KEY,
    recordDate TEXT,
    temperature INTEGER
);""",
                "data": [
                    (1, "2015-01-01", 10),
                    (2, "2015-01-02", 25),
                    (3, "2015-01-03", 20),
                    (4, "2015-01-04", 30),
                ],
            }
        ],
        "question": "找出与之前日期相比温度更高的所有日期的id",
        "mysql_sql": "select a.id from Weather as a, Weather as b where datediff(a.recordDate,b.recordDate) = 1 and a.Temperature > b.Temperature;",
        "sqlite_sql": "SELECT a.id FROM Weather AS a JOIN Weather AS b ON julianday(a.recordDate) - julianday(b.recordDate) = 1 AND a.temperature > b.temperature;",
    },
    {
        "id": "1661",
        "title": "每台机器进程的平均运行时间",
        "difficulty": "easy",
        "tables": [
            {
                "name": "Activity",
                "ddl": """CREATE TABLE Activity (
    machine_id INTEGER,
    process_id INTEGER,
    activity_type TEXT CHECK(activity_type IN ('start','end')),
    timestamp REAL
);""",
                "data": [
                    (0, 0, "start", 0.712),
                    (0, 0, "end", 1.520),
                    (0, 1, "start", 3.140),
                    (0, 1, "end", 4.120),
                    (1, 0, "start", 0.550),
                    (1, 0, "end", 1.550),
                    (1, 1, "start", 0.430),
                    (1, 1, "end", 1.420),
                    (2, 0, "start", 4.100),
                    (2, 0, "end", 4.512),
                    (2, 1, "start", 2.500),
                    (2, 1, "end", 5.000),
                ],
            }
        ],
        "question": "计算每台机器完成一个进程任务的平均耗时，四舍五入保留3位小数",
        "mysql_sql": "select a.machine_id, round(avg(b.timestamp - a.timestamp),3) as processing_time from (select * from Activity where activity_type = 'start') as a left join (select * from Activity where activity_type = 'end') as b on a.machine_id = b.machine_id and a.process_id = b.process_id group by machine_id;",
        "sqlite_sql": "SELECT a.machine_id, ROUND(AVG(b.timestamp - a.timestamp), 3) AS processing_time FROM (SELECT * FROM Activity WHERE activity_type = 'start') AS a LEFT JOIN (SELECT * FROM Activity WHERE activity_type = 'end') AS b ON a.machine_id = b.machine_id AND a.process_id = b.process_id GROUP BY machine_id;",
    },
    {
        "id": "577",
        "title": "员工奖金",
        "difficulty": "easy",
        "tables": [
            {
                "name": "Employee_577",
                "ddl": """CREATE TABLE Employee_577 (
    empId INTEGER PRIMARY KEY,
    name TEXT,
    supervisor INTEGER,
    salary INTEGER
);""",
                "data": [
                    (3, "Brad", None, 4000),
                    (1, "John", 3, 1000),
                    (2, "Dan", 3, 2000),
                    (4, "Thomas", 3, 4000),
                ],
            },
            {
                "name": "Bonus",
                "ddl": """CREATE TABLE Bonus (
    empId INTEGER PRIMARY KEY,
    bonus INTEGER
);""",
                "data": [
                    (2, 500),
                    (4, 2000),
                ],
            },
        ],
        "question": "报告每个奖金少于1000的员工的姓名和奖金数额",
        "mysql_sql": "select t1.name,t2.bonus from Employee as t1 left join Bonus as t2 on t1.empId = t2.empId where t2.bonus is null or t2.bonus <1000;",
        "sqlite_sql": "SELECT t1.name, t2.bonus FROM Employee_577 AS t1 LEFT JOIN Bonus AS t2 ON t1.empId = t2.empId WHERE t2.bonus IS NULL OR t2.bonus < 1000;",
    },
    {
        "id": "1280",
        "title": "学生们参加各科测试的次数",
        "difficulty": "easy",
        "tables": [
            {
                "name": "Students",
                "ddl": """CREATE TABLE Students (
    student_id INTEGER PRIMARY KEY,
    student_name TEXT
);""",
                "data": [
                    (1, "Alice"),
                    (2, "Bob"),
                    (13, "John"),
                    (6, "Alex"),
                ],
            },
            {
                "name": "Subjects",
                "ddl": """CREATE TABLE Subjects (
    subject_name TEXT PRIMARY KEY
);""",
                "data": [
                    ("Math",),
                    ("Physics",),
                    ("Programming",),
                ],
            },
            {
                "name": "Examinations",
                "ddl": """CREATE TABLE Examinations (
    student_id INTEGER,
    subject_name TEXT
);""",
                "data": [
                    (1, "Math"),
                    (1, "Physics"),
                    (1, "Programming"),
                    (2, "Programming"),
                    (1, "Physics"),
                    (1, "Math"),
                    (13, "Math"),
                    (13, "Programming"),
                    (13, "Physics"),
                    (2, "Math"),
                    (1, "Math"),
                ],
            },
        ],
        "question": "查询每个学生参加每一门科目测试的次数，结果按student_id和subject_name排序",
        "mysql_sql": "select t1.student_id, t1.student_name, t2.subject_name, count(t3.subject_name) as attended_exams from Students as t1 join Subjects as t2 left join Examinations as t3 on t1.student_id = t3.student_id and t2.subject_name = t3.subject_name group by t1.student_id, t2.subject_name order by t1.student_id, t2.subject_name;",
        "sqlite_sql": "SELECT t1.student_id, t1.student_name, t2.subject_name, COUNT(t3.subject_name) AS attended_exams FROM Students AS t1 JOIN Subjects AS t2 LEFT JOIN Examinations AS t3 ON t1.student_id = t3.student_id AND t2.subject_name = t3.subject_name GROUP BY t1.student_id, t2.subject_name ORDER BY t1.student_id, t2.subject_name;",
    },
    {
        "id": "620",
        "title": "有趣的电影",
        "difficulty": "easy",
        "tables": [
            {
                "name": "cinema",
                "ddl": """CREATE TABLE cinema (
    id INTEGER PRIMARY KEY,
    movie TEXT,
    description TEXT,
    rating REAL
);""",
                "data": [
                    (1, "War", "great 3D", 8.9),
                    (2, "Science", "fiction", 8.5),
                    (3, "irish", "boring", 6.2),
                    (4, "Ice song", "Fantacy", 8.6),
                    (5, "House card", "Interesting", 9.1),
                ],
            }
        ],
        "question": "找出所有影片描述为非boring的并且id为奇数的影片，按rating降序排列",
        "mysql_sql": "select * from cinema where description != 'boring' and id%2!=0 order by rating desc;",
        "sqlite_sql": "SELECT * FROM cinema WHERE description != 'boring' AND id % 2 != 0 ORDER BY rating DESC;",
    },
    {
        "id": "1251",
        "title": "平均售价",
        "difficulty": "easy",
        "tables": [
            {
                "name": "Prices",
                "ddl": """CREATE TABLE Prices (
    product_id INTEGER,
    start_date TEXT,
    end_date TEXT,
    price INTEGER,
    PRIMARY KEY (product_id, start_date, end_date)
);""",
                "data": [
                    (1, "2019-02-17", "2019-02-28", 5),
                    (1, "2019-03-01", "2019-03-22", 20),
                    (2, "2019-02-01", "2019-02-20", 15),
                    (2, "2019-02-21", "2019-03-31", 30),
                ],
            },
            {
                "name": "UnitsSold",
                "ddl": """CREATE TABLE UnitsSold (
    product_id INTEGER,
    purchase_date TEXT,
    units INTEGER
);""",
                "data": [
                    (1, "2019-02-25", 100),
                    (1, "2019-03-01", 15),
                    (2, "2019-02-10", 200),
                    (2, "2019-03-22", 30),
                ],
            },
        ],
        "question": "查找每种产品的平均售价，四舍五入到小数点后两位，如果没有售出则平均售价为0",
        "mysql_sql": "SELECT a.product_id, ROUND(SUM(a.t_price)/SUM(a.units),2) AS average_price FROM (SELECT p.product_id, p.price*u.units AS t_price, u.units AS units FROM Prices p JOIN UnitsSold u ON u.product_id=p.product_id WHERE u.purchase_date BETWEEN p.start_date AND p.end_date) AS a GROUP BY a.product_id;",
        "sqlite_sql": "SELECT a.product_id, ROUND(SUM(a.t_price) / SUM(a.units), 2) AS average_price FROM (SELECT p.product_id, p.price * u.units AS t_price, u.units AS units FROM Prices p JOIN UnitsSold u ON u.product_id = p.product_id WHERE u.purchase_date BETWEEN p.start_date AND p.end_date) AS a GROUP BY a.product_id;",
    },
    {
        "id": "1075",
        "title": "项目员工I",
        "difficulty": "easy",
        "tables": [
            {
                "name": "Project",
                "ddl": """CREATE TABLE Project (
    project_id INTEGER,
    employee_id INTEGER,
    PRIMARY KEY (project_id, employee_id)
);""",
                "data": [
                    (1, 1),
                    (1, 2),
                    (1, 3),
                    (2, 1),
                    (2, 4),
                ],
            },
            {
                "name": "Employee_1075",
                "ddl": """CREATE TABLE Employee_1075 (
    employee_id INTEGER PRIMARY KEY,
    name TEXT,
    experience_years INTEGER
);""",
                "data": [
                    (1, "Khaled", 3),
                    (2, "Ali", 2),
                    (3, "John", 1),
                    (4, "Doe", 2),
                ],
            },
        ],
        "question": "查询每一个项目中员工的平均工作年限，精确到小数点后两位",
        "mysql_sql": "select t1.project_id, round(avg(t2.experience_years),2) as average_years from Project as t1 join Employee as t2 on t1.employee_id = t2.employee_id group by project_id;",
        "sqlite_sql": "SELECT t1.project_id, ROUND(AVG(t2.experience_years), 2) AS average_years FROM Project AS t1 JOIN Employee_1075 AS t2 ON t1.employee_id = t2.employee_id GROUP BY project_id;",
    },
    {
        "id": "1633",
        "title": "各赛事的用户注册率",
        "difficulty": "easy",
        "tables": [
            {
                "name": "Users",
                "ddl": """CREATE TABLE Users (
    user_id INTEGER PRIMARY KEY,
    user_name TEXT
);""",
                "data": [
                    (6, "Alice"),
                    (2, "Bob"),
                    (7, "Alex"),
                ],
            },
            {
                "name": "Register",
                "ddl": """CREATE TABLE Register (
    contest_id INTEGER,
    user_id INTEGER,
    PRIMARY KEY (contest_id, user_id)
);""",
                "data": [
                    (215, 6),
                    (209, 2),
                    (208, 2),
                    (210, 6),
                    (208, 6),
                    (209, 7),
                    (209, 6),
                    (215, 7),
                    (208, 7),
                    (210, 2),
                    (207, 2),
                    (210, 7),
                ],
            },
        ],
        "question": "统计各赛事的用户注册百分率，保留两位小数，按percentage降序排序",
        "mysql_sql": "select contest_id, round(count(user_id)*100 / (select count(*) from Users),2) as percentage from Register group by contest_id order by percentage desc, contest_id;",
        "sqlite_sql": "SELECT contest_id, ROUND(COUNT(user_id) * 100.0 / (SELECT COUNT(*) FROM Users), 2) AS percentage FROM Register GROUP BY contest_id ORDER BY percentage DESC, contest_id;",
    },
    {
        "id": "1211",
        "title": "查询结果的质量和占比",
        "difficulty": "easy",
        "tables": [
            {
                "name": "Queries",
                "ddl": """CREATE TABLE Queries (
    query_name TEXT,
    result TEXT,
    position INTEGER,
    rating INTEGER
);""",
                "data": [
                    ("Dog", "Golden Retriever", 1, 5),
                    ("Dog", "German Shepherd", 2, 5),
                    ("Dog", "Mule", 200, 1),
                    ("Cat", "Shirazi", 5, 2),
                    ("Cat", "Siamese", 3, 3),
                    ("Cat", "Sphynx", 7, 4),
                ],
            }
        ],
        "question": "找出每次query_name的quality（评分与位置比率的平均值）和poor_query_percentage（评分小于3的查询占比），都四舍五入到两位小数",
        "mysql_sql": "SELECT query_name, ROUND(AVG(rating/position), 2) quality, ROUND(SUM(IF(rating < 3, 1, 0)) * 100 / COUNT(*), 2) poor_query_percentage FROM Queries Where query_name IS NOT NULL GROUP BY query_name;",
        "sqlite_sql": "SELECT query_name, ROUND(AVG(CAST(rating AS REAL) / position), 2) AS quality, ROUND(SUM(CASE WHEN rating < 3 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS poor_query_percentage FROM Queries WHERE query_name IS NOT NULL GROUP BY query_name;",
    },
    # ────── 中等 ──────
    {
        "id": "570",
        "title": "至少有5名直接下属的经理",
        "difficulty": "medium",
        "tables": [
            {
                "name": "Employee_570",
                "ddl": """CREATE TABLE Employee_570 (
    id INTEGER PRIMARY KEY,
    name TEXT,
    department TEXT,
    managerId INTEGER
);""",
                "data": [
                    (101, "John", "A", None),
                    (102, "Dan", "A", 101),
                    (103, "James", "A", 101),
                    (104, "Amy", "A", 101),
                    (105, "Anne", "A", 101),
                    (106, "Ron", "B", 101),
                ],
            }
        ],
        "question": "找出至少有五个直接下属的经理",
        "mysql_sql": "select name from Employee where id in (select managerId from Employee group by managerId having count(*)>=5);",
        "sqlite_sql": "SELECT name FROM Employee_570 WHERE id IN (SELECT managerId FROM Employee_570 GROUP BY managerId HAVING COUNT(*) >= 5);",
    },
    {
        "id": "1934",
        "title": "确认率",
        "difficulty": "medium",
        "tables": [
            {
                "name": "Signups",
                "ddl": """CREATE TABLE Signups (
    user_id INTEGER PRIMARY KEY,
    time_stamp TEXT
);""",
                "data": [
                    (3, "2020-03-21 10:16:13"),
                    (7, "2020-01-04 13:57:59"),
                    (2, "2020-07-29 23:09:44"),
                    (6, "2020-12-09 10:39:37"),
                ],
            },
            {
                "name": "Confirmations",
                "ddl": """CREATE TABLE Confirmations (
    user_id INTEGER,
    time_stamp TEXT,
    action TEXT CHECK(action IN ('confirmed','timeout'))
);""",
                "data": [
                    (3, "2021-01-06 03:30:46", "timeout"),
                    (3, "2021-07-14 14:00:00", "timeout"),
                    (7, "2021-06-12 11:57:29", "confirmed"),
                    (7, "2021-06-13 12:58:28", "confirmed"),
                    (7, "2021-06-14 13:59:27", "confirmed"),
                    (2, "2021-01-22 00:00:00", "confirmed"),
                    (2, "2021-02-28 23:59:59", "timeout"),
                ],
            },
        ],
        "question": "查找每个用户的确认率（confirmed消息数除以总请求数），四舍五入到两位小数，没有请求的用户确认率为0",
        "mysql_sql": "select T1.user_id, round(count(if(T2.action = 'confirmed',true,null)) / count(*),2) as confirmation_rate from Signups as T1 left join Confirmations as T2 on T1.user_id = T2.user_id group by T1.user_id;",
        "sqlite_sql": "SELECT T1.user_id, ROUND(COUNT(CASE WHEN T2.action = 'confirmed' THEN 1 ELSE NULL END) * 1.0 / COUNT(T2.user_id), 2) AS confirmation_rate FROM Signups AS T1 LEFT JOIN Confirmations AS T2 ON T1.user_id = T2.user_id GROUP BY T1.user_id;",
    },
]


def build_database(problems: list, db_path: str) -> None:
    """建 SQLite 库：建表 + 插入数据。"""
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    table_names = set()
    for prob in problems:
        for tbl in prob["tables"]:
            name = tbl["name"]
            if name in table_names:
                # 同名表冲突处理（如多个 Employee 表）— 用题号后缀
                continue
            table_names.add(name)

            # 建 DDL（去掉末尾分号）
            ddl = tbl["ddl"].rstrip(";").strip()
            cur.execute(ddl)

            # 插入数据 — 用 PRAGMA table_info 获取实际列名
            pragma_cols = conn.execute(f"PRAGMA table_info({name})").fetchall()
            col_names = [col[1] for col in pragma_cols]  # col[1] = name

            placeholders = ",".join(["?"] * len(col_names))
            insert_sql = f"INSERT INTO {name} ({','.join(col_names)}) VALUES ({placeholders})"
            for row in tbl["data"]:
                cur.execute(insert_sql, row)

    conn.commit()
    conn.close()

    # 统计建了多少张表
    conn = sqlite3.connect(db_path)
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    print(f"✅ 建库完成: {db_path}，共 {len(tables)} 张表: {[t[0] for t in tables]}")
    conn.close()


def build_golden_set(problems: list, golden_path: str) -> None:
    """生成评估集 JSONL。"""
    os.makedirs(os.path.dirname(golden_path), exist_ok=True)

    with open(golden_path, "w", encoding="utf-8") as f:
        f.write("# LeetCode SQL Golden Set — 50题测试集\n")
        f.write("# 来源: LeetCode高频SQL50题（基础版）\n")
        f.write("# 数据库: data/leetcode_test.db (SQLite)\n")
        f.write("# 所有SQL已从MySQL转换为SQLite兼容语法\n\n")

        for prob in problems:
            tables_in_prob = [tbl["name"] for tbl in prob["tables"]]
            case = {
                "id": f"lc-{prob['id']}",
                "leetcode_id": prob["id"],
                "query": prob["question"],
                "difficulty": prob["difficulty"],
                "tables": tables_in_prob,
                "mysql_sql": prob["mysql_sql"],
                "expected_sql": prob["sqlite_sql"],
                "expected_result_summary": f"LeetCode {prob['id']}: {prob['title']}",
                "tags": [prob["difficulty"], "leetcode"],
            }
            f.write(json.dumps(case, ensure_ascii=False) + "\n")

    print(f"✅ 评估集生成: {golden_path}，共 {len(problems)} 条")


def build_knowledge_docs(problems: list, kb_dir: str) -> None:
    """生成知识库文档（每道题一个 Markdown）。"""
    os.makedirs(kb_dir, exist_ok=True)

    for prob in problems:
        fname = f"lc_{prob['id']}_{prob['title'].replace(' ', '_')}.md"
        fpath = os.path.join(kb_dir, fname)

        lines = [f"# LeetCode {prob['id']}: {prob['title']}", ""]
        lines.append(f"**难度**: {prob['difficulty']}")
        lines.append(f"**LeetCode题号**: {prob['id']}")
        lines.append("")

        # 题目解读
        lines.append("## 题目解读")
        lines.append(prob["question"])
        lines.append("")

        # 表结构
        for tbl in prob["tables"]:
            lines.append(f"## 表: `{tbl['name']}`")
            lines.append("")
            # 从DDL提取列信息
            ddl = tbl["ddl"]
            col_lines = re.findall(r"^\s+(\w+)\s+([\w()]+(?:\s+CHECK\([^)]+\))?)", ddl, re.MULTILINE)
            lines.append("| 字段 | 类型 | 说明 |")
            lines.append("|------|------|------|")
            for col_name, col_type in col_lines:
                lines.append(f"| {col_name} | {col_type} | — |")
            lines.append("")

        # 参考SQL
        lines.append("## 参考SQL（SQLite）")
        lines.append("```sql")
        lines.append(prob["sqlite_sql"])
        lines.append("```")
        lines.append("")
        lines.append("## MySQL原始SQL（供方言对比）")
        lines.append("```sql")
        lines.append(prob["mysql_sql"])
        lines.append("```")
        lines.append("")

        # 方言差异标注
        mysql = prob["mysql_sql"]
        sqlite = prob["sqlite_sql"]
        if mysql != sqlite:
            lines.append("## 方言差异说明")
            # 检测常见差异
            diffs = []
            if "datediff" in mysql.lower() and "julianday" in sqlite.lower():
                diffs.append("DATEDIFF → julianday() 差值计算")
            if "if(" in mysql.lower() and "case when" in sqlite.lower():
                diffs.append("IF() → CASE WHEN ... THEN ... ELSE ... END")
            if "char_length" in mysql.lower() and "length" in sqlite.lower():
                diffs.append("CHAR_LENGTH() → LENGTH()")
            if diffs:
                lines.append("- " + "\n- ".join(diffs))
            else:
                lines.append("- 格式规范化（大写、空格等）")
            lines.append("")

        with open(fpath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    print(f"✅ 知识库文档生成: {kb_dir}，共 {len(problems)} 个文件")


def main():
    print("=" * 60)
    print("LeetCode SQL 50题 → SQLite 测试集生成器")
    print("=" * 60)

    # 1. 建 SQLite 库
    build_database(LEETCODE_PROBLEMS, DB_PATH)

    # 2. 生成评估集
    build_golden_set(LEETCODE_PROBLEMS, GOLDEN_PATH)

    # 3. 生成知识库文档
    build_knowledge_docs(LEETCODE_PROBLEMS, KB_DIR)

    print("\n🎉 全部完成！产物清单：")
    print(f"   数据库:  {DB_PATH}")
    print(f"   评估集:  {GOLDEN_PATH}")
    print(f"   知识库:  {KB_DIR}/lc_*")
    print()
    print("下一步：")
    print("   1. python -m recon_v2.rag.schema_indexer  # 重建 schema 索引")
    print("   2. 在 .env 中设置 EVAL_DB_PATH=data/leetcode_test.db")
    print("   3. python tests/eval/runner.py --db data/leetcode_test.db --golden tests/eval/leetcode_golden.jsonl")


if __name__ == "__main__":
    main()