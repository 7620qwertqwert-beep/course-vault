# 📚 Course Vault · 课程资料共享库

> 一门课的资料，不该让每一届都从零攒起。

这里是面向本校同学的开放课程资料库：**笔记、作业、实验报告、课件、历年试卷、复习攻略**，全部由历届同学自愿上传，永久免费、非营利。

灵感来自浙大「课程攻略共享计划」、电子科大「uestc-course」等国内高校开源资料库（见文末致谢）。

---

## ✨ 这个仓库解决什么问题

| 痛点 | 这里的答案 |
|---|---|
| 期末不知考什么 | 历年试卷 + 复习攻略 + 考点清单 |
| 老师 PPT 课后就删 | 课件按课程归档，随时回看 |
| 实验报告格式不会写 | 参考往届同款报告的结构与评分点 |
| 资料只在群里刷屏 | 一次上传，长期可查，不丢不沉 |
| 选课前不知道水深 | 课程 README 里写清考核方式、难度、避坑点 |

---

## 📂 目录结构

```
courses/
├── 公共课/
│   └── 高等数学/            # 一门课一个文件夹，用中文课名
│       ├── README.md        # 课程卡片（必填，见模板）
│       ├── notes/           # 笔记、错题、知识点整理
│       ├── slides/          # 课件 PPT / PDF
│       ├── exams/           # 历年试卷、模拟卷、答案
│       └── homework/        # 作业、实验报告（参考件）
└── 专业课/
    └── <课名>/
```

新建课程：复制 `_template/` 整个文件夹 → 改名成课程名 → 填好 README → 上传。

---

## 🚀 三种上传方式（挑一个顺手的）

### 方式一：网页直传（零基础，推荐）
1. 打开本仓库页面，进到目标课程文件夹
2. 点 `Add file` → `Upload files`
3. 拖进你的文件，底下填一句说明，点 `Commit changes`

### 方式二：Git 命令（适合批量）
```bash
git clone https://github.com/7620qwertqwert-beep/course-vault.git
cd course-vault
# 把文件丢进对应 courses/ 目录
git add .
git commit -m "add: 高等数学 2025秋期末试卷"  
git push
```

### 方式三：Issue 投递（不想碰仓库）
开一个 Issue，标题写课程名，正文贴网盘链接或直接拖文件进来，维护者会帮你归档。

---

## 📝 命名规范（重要，不然会乱成一锅粥）

```
<年份><学期>-<类型>-<描述>.<后缀>

例：
2025秋-期末-高等数学A卷.pdf
2025秋-期末-高等数学A卷-参考答案.pdf
2026春-笔记-第一章极限.md
2025秋-实验报告-测量数据处理.docx
```

- 学期：`春` / `秋`
- 类型固定用：`期末` `期中` `笔记` `作业` `实验` `课件` `复习` `答案`
- 文件名不带姓名、学号、老师私人联系方式

---

## 🚫 不要上传这些

- 带**个人姓名、学号、身份证号、联系方式**的任何文件（提交前一定检查页眉页脚和文档属性）
- 老师明确声明**不得外传**的材料（内部讲义、未公开试卷）
- 受版权保护的**正版教材 PDF**（教材只登记书名 + ISBN + 版本，不传全文）
- 单个超过 **50 MB** 的文件（走 Issue 给网盘链接）

---

## ⚖️ 免责声明

- 本仓库仅供**学习交流**，所有内容版权归原作者/原授课教师所有。
- 如权利人认为内容侵权，请开 Issue，维护者会在 48 小时内删除。
- 使用资料产生的任何后果（包括但不限于考试成绩）由使用者自负。
- 本仓库不保证资料的正确性、完整性、时效性，**仅供参考**。

---

## 🙏 致谢

结构与运营思路参考了这些优秀开源项目：

- [QSCTech/zju-icicles](https://github.com/QSCTech/zju-icicles) — 浙江大学课程攻略共享计划
- [Xovee/uestc-course](https://github.com/Xovee/uestc-course) — 电子科技大学课程资料
- [tianyilt/ecust-CourseShare](https://github.com/tianyilt/ecust-CourseShare) — 华理课程攻略共享库
- [BetterECNU/SharedResources](https://github.com/BetterECNU/SharedResources) — 原始资源与整理层分离的做法
- [mewcoder/SharedCourses](https://github.com/mewcoder/SharedCourses) — 全国高校资料库索引

---

## 📄 License

内容采用 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/deed.zh) 协议：署名、非商业、相同方式共享。

**欢迎 star，欢迎 PR，欢迎把你的资料留下来。**
