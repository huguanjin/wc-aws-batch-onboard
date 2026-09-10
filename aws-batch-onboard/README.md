# New API AWS 批量上号面板

面向 **New API 渠道类型 AWS（type = 33）** 的本地可视化上号工具。把 AWS Bedrock 账号批量写成 New API 渠道，并显示每条进度。

## 支持的密钥格式

和 New API 后台「AWS Key Format」一致：

| 密钥格式 | 填写方式 |
| --- | --- |
| AccessKey / SecretAccessKey | `AK|SK|Region` |
| API Key | `APIKey|Region` |

也支持逗号、空白分隔，以及 CSV。某一行没写 Region 时，会用页面上的默认区域补上。

## 启动

需要 Python 3.10+，无第三方依赖。

```bash
cd /Users/liuyucen/Desktop/github/aws-batch-onboard
python3 server.py
```

浏览器打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)

## 使用前准备

1. 打开你的 New API 后台
2. 复制 **个人设置 → 系统访问令牌**
3. 填写该账号的 **用户 ID**（请求头 `New-Api-User` 必须和令牌所属用户一致）
4. 管理员权限才能创建渠道

## 上号模式

- **逐条创建**：每个账号一个 AWS 渠道，适合看实时进度和失败原因
- **一次批量提交**：调用 New API 原生 `mode=batch`
- **多密钥合成一个渠道**：调用 `mode=multi_to_single`

创建时会写入：

```json
{
  "type": 33,
  "settings": { "aws_key_type": "ak_sk" }
}
```

可选：默认勾选「测活通过后再加入号池」。渠道会先以禁用状态写入，调用 `/api/channel/test/:id` 测活成功后才启用进路由池。失败的保持禁用，也可选择直接删除。

## 安全说明

- 服务只监听 `127.0.0.1`
- 代理只转发 `/api/` 接口
- 请只对自己有管理权限的 New API 实例操作
- 不要把访问令牌和 AWS 密钥提交到公开仓库
