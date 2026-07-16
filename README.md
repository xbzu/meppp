# MEPPP

MEPPP 是一个面向小型社区的轻量内容与互动系统，目前处于独立重写阶段。

## 技术方向

- Python 3.14
- Django 5.2 LTS
- Django Templates + 原生渐进增强，HTMX 按需引入
- SQLite 默认存储，可迁移至 PostgreSQL
- 模块化单体：一个应用进程、一个代码库、一个默认数据库

## 当前状态

独立基础架构、首版公开 UI、举报处置后台和单容器生产运行方案已经纳入同一代码库。每次变更均由 GitHub Actions 检查代码质量、数据库迁移、应用测试、浏览器流程、生产安全配置和容器健康。

现有基础包括自定义用户、内容与互动模型、通知、带审计记录的举报处置、版本化站点配置、应用层只追加的审计记录、Django Admin、健康检查、SQLite 在线备份与恢复演练，以及受资源限制的单容器构建。

首版公开界面已经覆盖：

- 按时间排列的信息流、搜索、话题筛选和关注流
- 独立成员登录、受配置控制的注册和 POST 登出
- 文本发布、预审核状态、详情、平铺评论、点赞和关注
- 成员公开主页、通知中心和绑定真实对象的举报入口
- 桌面与手机响应式布局、CSP、安全缓存边界和双维度限流

成员图片上传暂时保持关闭。模型已预留四图和替代文本，但必须完成真实图片解码、重编码、像素限制和失败清理后才会开放。

## 开发检查

```bash
uv sync --locked --all-groups
uv run ruff check .
uv run python manage.py makemigrations --check --dry-run
uv run coverage run manage.py test meppp
uv run coverage report
uv run python -m playwright install chromium
uv run python manage.py test tests.browser
```

这些命令只安装开发依赖、检查源码并使用临时测试数据库，不会启动服务。

## 项目原则

- 优先长期可维护性，不引入不必要的微服务、队列、缓存或搜索服务。
- 前台、管理后台和业务规则共用同一套领域服务。
- 从第一天保留审核、审计、配置回滚和数据库迁移边界。
- 本项目为独立实现，只采用通用产品思想和独立编写的需求，不导入其他项目的源码、数据结构、文案、资产或 Git 历史。

更多说明：

- [独立实现边界](docs/CLEAN_ROOM.md)
- [产品范围](docs/PRODUCT_SCOPE.md)
- [架构](docs/ARCHITECTURE.md)
- [UI 规范](docs/UI_SPEC.md)
- [运行与部署边界](docs/OPERATIONS.md)
- [生产部署包](deploy/README.md)

## License

[MIT](LICENSE)
