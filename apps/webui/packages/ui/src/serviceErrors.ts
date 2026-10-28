/** User-facing transport failures, shared by the WebUI service clients. */
export function serviceFailure(service: string, status: number): string {
  const code = `（${status}）`;
  if (status === 401) return `${service}认证失败${code}，请检查登录状态或服务密钥。`;
  if (status === 403) return `${service}拒绝访问${code}，请检查服务授权。`;
  if (status === 408 || status === 504) return `${service}响应超时${code}，请稍后重试。`;
  if (status === 413) return `上传内容过大${code}，请减小文件后重试。`;
  if (status === 429) return `${service}请求过于频繁${code}，请稍后重试。`;
  if (status === 502 || status === 503)
    return `${service}暂不可用${code}，请确认服务已启动后重试。`;
  if (status >= 500) return `${service}处理失败${code}，请稍后重试。`;
  return `${service}请求未完成${code}，请检查输入后重试。`;
}

export function taskFailure(message: string): string {
  if (/\b401\b|authentication|invalid.?api.?key|unauthorized/i.test(message))
    return '模型服务认证失败，请检查模型密钥和访问权限后重试。';
  if (/\b402\b|insufficient.{0,20}(quota|balance|credit)|余额不足/i.test(message))
    return '模型服务额度不足，请检查账户余额或配额后重试。';
  if (/\b403\b|permission.?denied|forbidden/i.test(message))
    return '模型服务拒绝访问，请检查模型和接口的使用权限。';
  if (/\b429\b|rate.?limit|too many requests/i.test(message))
    return '模型服务请求过于频繁，请稍后重试。';
  if (/timeout|timed? ?out|超时/i.test(message))
    return '本次回答等待超时，请稍后重试；已生成的内容仍可查看。';
  if (
    /\b50[0234]\b|connection.{0,20}(error|refused|failed)|connecterror|failed to fetch|network.?error/i.test(
      message,
    )
  )
    return '模型服务暂时无法连接，请检查服务状态和网络后重试。';
  if (/context.{0,20}(length|window)|maximum.{0,20}tokens|上下文.{0,5}(超|长)/i.test(message))
    return '本次对话超出模型的上下文限制，请缩短内容或开启新对话。';
  return '本次回答未完成，请稍后重试；已生成的内容仍可查看。';
}
