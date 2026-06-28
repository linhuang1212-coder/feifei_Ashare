# 本地部署指南(给朋友:不需要 China_quant)

这套系统可以**完全独立**在你自己电脑上跑——不需要原作者那个 10GB 的 China_quant 数据库,只要 **Python + 一个 tushare token**,跑一条建库命令就行。全市场历史数据由 tushare 现拉。

> 适用:Windows(本文以 Windows 为例;Mac/Linux 把路径和 .bat 换成 shell 即可)。

---

## 一、前置

1. **Python 3.12**(装时勾选 Add to PATH,或记住安装路径)。
2. **tushare 账号 + token**:注册 https://tushare.pro,**积分需 ≥ 2000**(才能调 `adj_factor`/`daily_basic`;通常捐赠 200 元/年可得)。token 在「个人主页」。

---

## 二、装好代码与依赖

```bat
git clone https://github.com/linhuang1212-coder/feifei_Ashare.git
cd feifei_Ashare
pip install -r requirements.txt
```
> 用哪个 python 自己清楚即可;下文 `py` 指你的 Python 3.12。

---

## 三、填 tushare token

把 `secrets.local.yaml.example` 复制为 `secrets.local.yaml`,填上你的 token:
```yaml
tushare_token: "你的token"
feishu_webhook: ""
```
> 这个文件已被 .gitignore,不会进 git。也可改用环境变量 `TUSHARE_TOKEN`。

---

## 四、建全市场缓存(关键一步,约 10–15 分钟)

```bat
py run.py cache --from tushare
```
- 它会用 tushare **按交易日批量**拉全市场(2023 年至今)前复权日线,建成本地缓存 `data\feifei.db`(约 500–600 MB)。
- 约 2500 次批量调用(一次拿全市场),受 tushare 频率限制会自动等待重试,耐心等它跑完。
- 看到 `tushare 建库完成:XXXX 行` 即成功。

> 之后每个交易日收盘后跑 `py run.py update` 增量补到最新(约 5 次调用、几秒)。

---

## 五、用起来

```bat
py run.py score              # 自选股打分 + 信号 + 大盘环境
py run.py check 600519       # 某股指标达标清单
py run.py screen --top 30    # 全市场扫描选股
py -m streamlit run app.py   # 仪表盘,浏览器开 http://localhost:8501
```
完整用法见 [USAGE.md](USAGE.md)。

---

## 六、改成你自己的自选股

编辑 `config.yaml` 的 `watchlist`(代码 + 名字;持有的填 cost_price):
```yaml
watchlist:
  - { code: "600519", name: "贵州茅台" }
  - { code: "000001", name: "平安银行", position_status: HOLDING, cost_price: 11.5 }
```

> `config.yaml` 里 `data.local_db_path` 默认指向原作者的 China_quant 路径——**你没有那个库,留着不用管**(系统会自动走 tushare 缓存;板块用仓库自带的 `ref/sw_l1_member.csv`)。

---

## 七、(可选)每天收盘自动更新

`daily.bat` 和 `register_task.bat` 里的路径是按原作者的 `C:\Users\Administrator\feifei_Ashare` 写死的,**你要改成自己的安装路径**:
1. 用记事本打开 `daily.bat`,把 `set PY=...` 和 `set REPO=...` 改成你的 Python 和项目路径。
2. 打开 `register_task.bat`,把里面的 `daily.bat` 路径改成你的。
3. 右键 `register_task.bat` →「以管理员身份运行」,即注册工作日 18:00 自动跑。

---

## 八、注意

- 第一次建库慢(拉全历史),之后都是几秒的增量 `update`。
- tushare 不按调用扣费,积分是等级/频率门槛;日常用量极小,基本零成本。
- 周末/节假日休市,数据停在最后交易日属正常。
- 所有信号仅为技术指标计算结果,**不构成投资建议**,风险自负(见 USAGE.md 免责)。
