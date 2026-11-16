# 05-proofpack（核证匣）

Proofpack — 举证放行：采样抽取 → 证据打包 → 校验清单 → 放行/驳回闭环。

与 Feedstitch（多源归并）、Batchgate（作业编排）、Syncledger（双端对账）、Changecast（CDC 投影）主链路不同。夹具全本地，不连活库。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5500 |
| API | http://localhost:10500 |

## 主链

选举证对象 → 按固定种子采样 → 生成证据包（含指纹）→ 跑基础校验清单 → 按部分失败命运处置 → 放行/驳回闭环

### 部分失败命运（互斥单选）

清单跑完若存在未通过项，包进入部分失败命运。策略在「设置」中互斥单选（`partial_fail_policy`），不存在并存开关：

- **整包驳回口径（reject_all）**：未通过项全部保留为 `failed`，包进入 `partial_rejected`（不可放行/须驳回态）。禁止放行，只能驳回留因或修复数据后重跑清单（整包复检）。
- **分项挂起（suspend_items）**：未通过项标 `suspended`、已通过项保留，包进入 `partial_suspended`。禁止放行，但允许对挂起项逐项**复检**或**豁免**（豁免强制留原因，全程入 `checklist_actions` 留痕）；挂起全部闭环后回到 `checked_ok` 才可放行。

铁律：只要仍有未通过项或未解决挂起，服务端一律拒绝写出放行通过；未通过项列表、包状态、放行拒绝原因同源于 `checklist_results`。

包状态机：`built → checked_ok | partial_rejected | partial_suspended → released | rejected`。

## 种子举证对象

- 门店日结抽检
- 渠道结算抽检
- 价目变更核证

底座内置基础校验：条数一致、键唯一、必填非空。影子试跑 / 规则版本表空挂，深逻辑留给后题。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 本地后端

```bash
cd backend
pip install -r requirements.txt
set DATA_DIR=%TEMP%\proofpack-data
uvicorn app.main:app --host 127.0.0.1 --port 10500
```
