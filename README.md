# 网页红井测试版一键部署

网页红警一键部署包/chronodivide asssets。网页红井（网页版“红警”/Chronodivide）预览版测试站点，帮助各类爱好者一键搭建自己的网页版“红警”站点，助力营销和知识分享

## 一键部署

### Github托管页面

fork该项目，命名为 你的名字.github.io，例如 ra2web.github.io

正如你所见，这个项目的名字就符合这个域名规则。那么，此时的你可以访问 https://ra2web.github.io 来游玩网页红井拉

### Vercel

[![一键部署到Vercel](https://vercel.com/button)](https://vercel.com/import/project?template=https://github.com/ra2web/ra2web.github.io)

## 多语言

站点默认简体中文,页面右上角可切换 简体中文 / 繁體中文 / English,
选择会保存在浏览器本地;实现见 `lib/lang-switch.js`。

## 自建联机后端

`backend/` 目录提供 Python(Django)实现的完整联机后端
(大厅、对战中继、天梯、战绩),与前端无缝对接,
部署方法与接口说明见 [backend/README.md](backend/README.md)。



