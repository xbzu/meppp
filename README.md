# MEPPP

MEPPP 是一个面向小型社区的轻量内容与互动系统，目前处于独立重写阶段。

## 技术方向

- Python 3.14
- Django 5.2 LTS
- Django Templates + HTMX
- SQLite 默认存储，可迁移至 PostgreSQL
- 模块化单体：一个应用进程、一个代码库、一个默认数据库

## 当前状态

独立基础架构正在 Draft Pull Request 中实现，并由 GitHub Actions 验证；当前不进行本地或服务器部署。

现有基础包括自定义用户、内容与互动模型、通知、举报审核、版本化站点配置、应用层只追加的审计记录、Django Admin、健康检查和单容器构建。

## 开发检查

```bash
uv sync --locked --all-groups
uv run ruff check .
uv run python manage.py makemigrations --check --dry-run
uv run coverage run manage.py test meppp
uv run coverage report
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
- [运行与部署边界](docs/OPERATIONS.md)

## License

[MIT](LICENSE)
