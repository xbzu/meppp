# MEPPP

MEPPP 是一个面向小型社区的轻量内容与互动系统，目前处于独立重写阶段。

## 技术方向

- Python 3.14
- Django 5.2 LTS
- Django Templates + 原生渐进增强，HTMX 按需引入
- SQLite 默认存储，可迁移至 PostgreSQL
- 模块化单体：一个应用进程、一个代码库、一个默认数据库

## 当前状态

独立基础架构、开放/邀请注册社区闭环、公开 UI、运营后台和单容器生产运行方案已经纳入同一代码库。每次变更均由 GitHub Actions 检查代码质量、数据库迁移、应用测试、浏览器流程、生产安全配置和容器健康。

现有基础包括自定义用户、单次邀请码、内容与互动模型、通知、待审内容队列、带审计记录的举报处置、版本化站点配置、运营总览、代码化权限组、应用层只追加的审计记录、健康检查、SQLite 在线备份与恢复演练，以及受资源限制的单容器构建。

首版公开界面已经覆盖：

- 按时间排列的信息流、搜索、话题筛选和关注流
- 独立成员登录、开放/邀请/关闭注册、一次性账号恢复码和 POST 登出
- 可过期、撤销、绑定邮箱且防重复领取的单次邀请码
- 文本发布、预审核状态、详情、平铺评论、点赞和关注
- 每帖最多四张安全图片：真实解码、EXIF 方向校正、元数据清除、像素限制并统一重编码为 WebP
- 每帖一个不超过 20 MB、5 分钟的安全视频：严格编码检查、FFmpeg 重封装、元数据清除、WebP 封面和 Range 播放
- X / YouTube 官方链接导入：本地规范化 ID、固定官方 oEmbed、可追溯署名卡片和 YouTube 隐私增强播放器；不下载第三方媒体
- 成员公开主页、“我的社区”、资料与密码管理、作者撤回
- 带审核结论与理由的通知中心、绑定真实对象的举报入口
- 待审内容/评论专用队列、运营总览和“运营/审核”最小权限组
- 桌面与手机响应式布局、CSP、安全缓存边界和双维度限流

图片、视频和视频封面保存在同一个受备份的 `/data` 数据边界中，但不直接公开映射媒体目录。每次读取都会重新核对内容状态和作者状态；待审、隐藏或撤回内容不能绕过审核继续公开媒体。成员头像上传仍保持关闭。

## 开发检查

```bash
uv sync --locked --all-groups
uv run ruff check .
uv run python manage.py makemigrations --check --dry-run
uv run coverage run manage.py test meppp
uv run coverage report
uv run python -m playwright install chromium
MEPPP_TEST_DATABASE_PATH=/tmp/meppp-browser-tests.sqlite3 uv run python manage.py test tests.browser
```

这些命令只安装开发依赖、检查源码并使用临时测试数据库，不会启动服务。

## 项目原则

- 优先长期可维护性，不引入不必要的微服务、队列、缓存或搜索服务。
- 前台、管理后台和业务规则共用同一套领域服务。
- 从第一天保留审核、审计、配置回滚和数据库迁移边界。
- 本项目的业务代码与数据模型为独立实现；前台信息层级参考了已固定版本的 MIT 开源界面，并在项目内重新编写 Django 模板与 CSS，不导入对方源码、数据结构、文案、品牌资产或 Git 历史。

更多说明：

- [独立实现边界](docs/CLEAN_ROOM.md)
- [产品范围](docs/PRODUCT_SCOPE.md)
- [架构](docs/ARCHITECTURE.md)
- [UI 规范](docs/UI_SPEC.md)
- [第三方设计参考与许可说明](THIRD_PARTY_NOTICES.md)
- [X / YouTube 引用导入边界](docs/EXTERNAL_SHARE_ROADMAP.md)
- [运行与部署边界](docs/OPERATIONS.md)
- [生产部署包](deploy/README.md)

## License

[MIT](LICENSE)
