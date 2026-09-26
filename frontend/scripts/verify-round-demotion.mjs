/**
 * 回归验证（前端 reducer 逻辑）：多轮工具调用下「最终答复被吞」缺陷。
 *
 * 背景：
 *   后端与前端各有一份「先当答复流式、事后降级」的定性逻辑，两边的键必须
 *   同为 **(message_id, 轮次)**。DeepSeek 整个 run 复用同一个 message_id
 *   （实测 `lc_run--01a0cd`），若只按 message_id 定性，首轮叙述会把后续每轮
 *   正文（含最终答复）全部吞掉 → 收尾 `答复=0字` → 前端空白答复气泡。
 *
 * 本文件把 `useRunStream.ts` 里的纯逻辑（roundKey / parseRoundKey /
 * bumpRounds / joinAnswer 与 chunk、retract、tool_end 三个分支）逐字复刻
 * 成可执行的 JS，在 Node 下直接跑断言，**无需启动浏览器或 Vite**。
 *
 * 用法：
 *   node frontend/scripts/verify-round-demotion.mjs
 */

import assert from 'node:assert/strict'

// ── 以下函数与 useRunStream.ts 中同名实现保持一致（修改时需同步）────────────
const ROUND_SEP = '#'

function roundKey(messageId, round) {
  return `${messageId}${ROUND_SEP}${round}`
}

function parseRoundKey(key) {
  const at = key.lastIndexOf(ROUND_SEP)
  if (at < 0) return { messageId: key, round: 0 }
  const round = Number(key.slice(at + 1))
  return { messageId: key.slice(0, at), round: Number.isFinite(round) ? round : 0 }
}

function bumpRounds(roundByMessage, messageIds) {
  const next = { ...roundByMessage }
  for (const id of new Set(messageIds)) next[id] = (next[id] ?? 0) + 1
  return next
}

function joinAnswer(answerByMessage, retracted) {
  return Object.keys(answerByMessage)
    .filter((key) => !retracted[key])
    .map((key) => answerByMessage[key])
    .join('')
}

// ── reducer 三分支的最小复刻 ────────────────────────────────────────────────
function makeState() {
  return { answerByMessage: {}, retracted: {}, roundByMessage: {} }
}

/** 对应 case 'chunk' */
function chunk(state, messageId, text) {
  const key = roundKey(messageId, state.roundByMessage[messageId] ?? 0)
  if (state.retracted[key]) return state // 已定性轮次 → 走叙述，不进答复
  const answerByMessage = {
    ...state.answerByMessage,
    [key]: (state.answerByMessage[key] ?? '') + text,
  }
  return { ...state, answerByMessage, aiText: joinAnswer(answerByMessage, state.retracted) }
}

/** 对应 case 'retract' */
function retract(state, messageId) {
  const key = roundKey(messageId, state.roundByMessage[messageId] ?? 0)
  if (state.retracted[key]) return state
  const retracted = { ...state.retracted, [key]: true }
  return { ...state, retracted, aiText: joinAnswer(state.answerByMessage, retracted) }
}

/** 对应 case 'tool_end' */
function toolEnd(state) {
  return {
    ...state,
    roundByMessage: bumpRounds(
      state.roundByMessage,
      Object.keys(state.answerByMessage).map((k) => parseRoundKey(k).messageId),
    ),
  }
}

// ── 断言 ────────────────────────────────────────────────────────────────────
const PASS = []
const FAIL = []

function check(name, fn) {
  try {
    fn()
    PASS.push(name)
    console.log(`  PASS ${name}`)
  } catch (err) {
    FAIL.push(name)
    console.log(`  FAIL ${name}\n       ${err.message}`)
  }
}

const MID = 'lc_run--01a0cd' // 关键：整轮复用同一个 message_id

console.log('\n[场景 A] 三轮都带工具调用（对应真实 run 121a869d）')
check('三轮均降级 → 可见答复为空（预期）', () => {
  let st = makeState()
  for (const t of ['我来生成图片。', '生图报错了，我再试一次。', '两次都失败了，请你定夺。']) {
    st = chunk(st, MID, t)
    st = retract(st, MID)
    st = toolEnd(st)
  }
  // 三轮都带工具，全部降级为叙述 → 答复为空是正确行为
  assert.equal(st.aiText, '')
  // 关键：轮次必须逐轮推进，而不是被首轮永久锁死
  assert.deepEqual(Object.keys(st.answerByMessage), [
    `${MID}#0`,
    `${MID}#1`,
    `${MID}#2`,
  ])
})

console.log('\n[场景 B] 末轮无工具调用（真实最终答复形态）')
check('末轮正文保留为可见答复', () => {
  let st = makeState()
  st = chunk(st, MID, '正在处理。')
  st = retract(st, MID)
  st = toolEnd(st)
  st = chunk(st, MID, '图片没能生成成功，原因是模型未开通。')
  assert.equal(st.aiText, '图片没能生成成功，原因是模型未开通。')
})

console.log('\n[场景 C] 两轮叙述 + 末轮答复')
check('仅末轮成为答复，轮次键连续无跳号', () => {
  let st = makeState()
  st = chunk(st, MID, 'n1')
  st = retract(st, MID)
  st = toolEnd(st)
  st = chunk(st, MID, 'n2')
  st = retract(st, MID)
  st = toolEnd(st)
  st = chunk(st, MID, 'FINAL')
  assert.equal(st.aiText, 'FINAL')
  // 回归：曾因 bumpRounds 未去重导致键跳号（#0,#1,#3）
  assert.deepEqual(Object.keys(st.answerByMessage), [
    `${MID}#0`,
    `${MID}#1`,
    `${MID}#2`,
  ])
})

console.log('\n[场景 D] 纯单元：轮次键解析')
check('parseRoundKey 正确还原 message_id 与轮次', () => {
  assert.deepEqual(parseRoundKey(`${MID}#7`), { messageId: MID, round: 7 })
  // 无分隔符时退化为第 0 轮
  assert.deepEqual(parseRoundKey('plain'), { messageId: 'plain', round: 0 })
})

console.log('\n[场景 E] 纯单元：bumpRounds 去重')
check('重复 message_id 只 +1 一次', () => {
  const next = bumpRounds({ M: 0 }, ['M', 'M', 'M'])
  assert.equal(next.M, 1)
})

console.log(`\n${'='.repeat(56)}`)
console.log(`通过 ${PASS.length} / ${PASS.length + FAIL.length}`)
if (FAIL.length) {
  console.log('失败项：')
  for (const name of FAIL) console.log(`  - ${name}`)
  process.exit(1)
}
console.log('全部通过：多轮降级按轮次隔离，最终答复不再被首轮吞掉。')
