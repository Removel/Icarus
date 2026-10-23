// chat.deepseek.com welcome_msg 配置及筛选逻辑，抓取于 2026-10-23。
export const welcomeMessages = [
  '你好，我能帮什么忙吗？',
  '你好，有什么我能帮你的吗？',
  '你好，让我们开始聊天吧',
  '嗨！今天想聊些什么？',
  '欢迎回来，随时开始吧',
  '欢迎回来，想从哪里开始？',
  '早上好，今天有什么计划？',
  '早上好，今天想做点什么？',
  '早上好，开始聊天吧',
  '早上好，今天想聊些什么？',
  '下午好，我能帮什么忙吗？',
  '晚上好，有什么我能帮你的吗？',
];

export function pickWelcomeMessage(date = new Date(), random = Math.random()) {
  const minutes = date.getHours() * 60 + date.getMinutes();
  const indices = [0, 1, 2, 3, 4, 5];
  if (minutes >= 360 && minutes < 660) indices.push(6, 7, 8, 9);
  else if (minutes >= 780 && minutes < 1050) indices.push(10);
  else if (minutes >= 1050 && minutes < 1320) indices.push(11);
  return welcomeMessages[indices[Math.floor(random * indices.length)]];
}
