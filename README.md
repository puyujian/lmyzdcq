# 懒猫云 VPS 自动重启

这是一个基于 `FastAPI + Playwright` 的 Docker 服务。

它对外暴露 HTTP 接口用于接收服务器状态；当状态判定为关机或离线时，会自动进入懒猫云控制台，检查登录态，必要时重新登录，然后进入实例面板，先执行停止，等待 5 秒，再执行启动。

## 已验证的站点公开链路

基于 2026-04-05 的站点检查：

- `https://lxc.lazycat.wiki/clientarea` 在未登录时会跳转到 `https://lxc.lazycat.wiki/login`
- 登录页邮箱输入框选择器是 `#emailInp`
- 登录页密码输入框选择器是 `#emailPwdInp`
- 登录按钮选择器是 `#loginButton`

## 目录结构

```text
app/
  config.py
  main.py
  models.py
  panel.py
  restart_manager.py
tests/
docker-compose.yml
Dockerfile
```

## 核心能力

- 登录态复用：`/data/storage-state.json` 持久化浏览器会话
- 登录失效自动重登：优先检查 `/clientarea`，失效时转去 `/login`
- 自动重启：进入实例详情页后点击“进入面板”，执行“停止 -> 等 5 秒 -> 启动”
- 冷却保护：默认 300 秒内成功重启过一次则跳过重复触发
- 并发保护：同一时间只允许一个重启任务执行
- 调试留痕：失败和成功后会在 `/data/artifacts` 存截图

## 启动

先复制一份环境变量模板：

```bash
cp .env.example .env
```

然后修改 `.env` 里的这些配置：

- `API_TOKEN`
- `LAZYCAT_EMAIL`
- `LAZYCAT_PASSWORD`
- `LAZYCAT_TARGET_HOSTNAME`
  - 当前只有一台实例时可以留空
  - 后续有多台时，建议直接填主机名，避免误点
- `HOST_PORT`
  - 默认是 `8080`
  - 如果宿主机端口被占用，可以改成比如 `18080`

然后启动：

```bash
docker compose up -d --build
```

## 接口

### 1. 健康检查

```http
GET /healthz
```

### 2. 状态上报

```http
POST /api/v1/vps/status
X-Api-Token: your-token
Content-Type: application/json
```

也兼容下面这种第三方 webhook 常见写法：

```http
api_key: your-token
Content-Type: application/json
```

#### 第三方 Webhook 平台配置示例

如果你的告警平台配置项是下面这种格式：

```text
接口地址
请求头[header]
请求体[body]
```

那就这样填：

接口地址：

```text
https://your-domain-url/api/v1/vps/status
```

请求方法：

```text
POST
```

请求头 `header`：

```json
{
  "api_key": "{{API_KEY}}",
  "Content-Type": "application/json"
}
```

请求体 `body`：

```json
{
  "status": "{{CONTENT}}",
  "source": "{{FROM}}",
  "instance_name": "my-vps-01"
}
```

字段填写说明：

- `https://your-domain-url`：替换成你的服务域名，或者 `http://服务器IP:端口`
- `{{API_KEY}}`：替换成 `.env` 里的 `API_TOKEN`
- `{{CONTENT}}`：建议传 `offline`、`down`、`off`、`shutdown`、`stopped`、`powered_off` 这些值之一
- `{{FROM}}`：替换成告警来源，比如 `uptime-kuma`、`zabbix`、`webhook`
- `instance_name`：替换成懒猫云里这台 VPS 的主机名；如果当前只有一台，也可以不传，改为在 `.env` 里固定 `LAZYCAT_TARGET_HOSTNAME`

一个完整可直接用的示例：

```json
{
  "status": "offline",
  "source": "uptime-kuma",
  "instance_name": "my-vps-01"
}
```

请求体示例：

```json
{
  "status": "offline",
  "instance_name": "my-vps",
  "source": "uptime-kuma",
  "metadata": {
    "ip": "1.2.3.4"
  }
}
```

如果上报里没有 `instance_name`，系统会回退到 `.env` 里的 `LAZYCAT_TARGET_HOSTNAME`。

触发重启的判定值包括：

- `offline`
- `down`
- `off`
- `shutdown`
- `stopped`
- `powered_off`
- `is_online=false`

### 3. 手动重启

```http
POST /api/v1/vps/restart
X-Api-Token: your-token
Content-Type: application/json
```

如果你要从外部平台直接手动触发重启，可以这样调用：

接口地址：

```text
https://your-domain-url/api/v1/vps/restart
```

请求方法：

```text
POST
```

请求头 `header`：

```json
{
  "api_key": "{{API_KEY}}",
  "Content-Type": "application/json"
}
```

```json
{
  "reason": "manual",
  "instance_name": "my-vps",
  "force": true
}
```

### 4. 查看任务结果

```http
GET /api/v1/jobs/{job_id}
X-Api-Token: your-token
```

## 兼容多实例和页面变更

如果后续不止一台 VPS，或者面板按钮文案变化，可以直接在 `.env` 里改这些环境变量，不用改代码：

- `LAZYCAT_TARGET_HOSTNAME`
- `LAZYCAT_SERVICE_LINK_SELECTORS`
- `LAZYCAT_ENTER_PANEL_SELECTORS`
- `LAZYCAT_STOP_BUTTON_SELECTORS`
- `LAZYCAT_START_BUTTON_SELECTORS`
- `LAZYCAT_CONFIRM_BUTTON_SELECTORS`
- `LAZYCAT_ENTER_PANEL_TEXTS`
- `LAZYCAT_STOP_BUTTON_TEXTS`
- `LAZYCAT_START_BUTTON_TEXTS`
- `LAZYCAT_CONFIRM_BUTTON_TEXTS`

优先级是：

1. 先用选择器
2. 再用按钮文字兜底

## 注意

- 当前代码已经按“先停止，等待 5 秒，再启动”实现
- 真实登录与面板 DOM 还需要你提供可用账号后做一次联调，尤其是实例详情页和面板页按钮文案可能因套餐页样式不同而略有差异
- 如果懒猫云后续增加验证码、二次验证或面板 iframe 结构变化，需要再补一轮适配
