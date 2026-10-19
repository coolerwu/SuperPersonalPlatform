# Contracts

v1 的 API 契约由 FastAPI 自动生成：

- 运行时：`GET /openapi.json`
- 文档：`GET /docs`

后续可在这里生成 TypeScript 类型，避免前后端手写重复类型。当前前端使用 `apps/web/src/types.ts` 的手写类型。
