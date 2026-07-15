# MEPPP

MEPPP 是一个面向小型社区的轻量内容与互动系统，目前处于独立重写阶段。

## 技术方向

- Python 3.14
- Django 5.2 LTS
- Django Templates + HTMX
- SQLite 默认存储，可迁移至 PostgreSQL
- 模块化单体：一个应用进程、一个代码库、一个默认数据库

## 当前状态

仓库刚完成独立项目初始化。功能基础将在 Draft Pull Request 中持续实现，并由 GitHub Actions 验证；当前不进行本地或服务器部署。

## 项目原则

- 优先长期可维护性，不引入不必要的微服务、队列、缓存或搜索服务。
- 前台、管理后台和业务规则共用同一套领域服务。
- 从第一天保留审核、审计、配置回滚和数据库迁移边界。
- 本项目为独立实现，只采用通用产品思想和独立编写的需求，不导入其他项目的源码、数据结构、文案、资产或 Git 历史。

完整边界见 [docs/CLEAN_ROOM.md](docs/CLEAN_ROOM.md)。

## License

[MIT](LICENSE)
