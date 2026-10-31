export const taskTypes = ['区域巡查', '物资护送', '关键设施保护', '受损设备撤离'] as const

export type TaskType = typeof taskTypes[number]
export type Side = 'red' | 'blue'
export type SideTask = {
  type: TaskType
  target: string
  deadlineStep: number
}
export type TaskPair = Record<Side, SideTask>
export type TaskErrors = { red: string; blue: string; shared: string }

const targetsByType: Record<TaskType, readonly string[]> = {
  区域巡查: ['A-B-C', 'C-B-A'],
  物资护送: ['A-B-C', 'C-B-A'],
  关键设施保护: ['设施01'],
  受损设备撤离: ['撤离点'],
}

export const taskFormatExample = {
  red: '区域巡查|A-B-C|60',
  blue: '区域巡查|C-B-A|60',
}

export function parseTaskLine(input: string): { task: SideTask | null; error: string } {
  const fields = input.trim().split('|').map(field => field.trim())
  if (fields.length !== 3 || fields.some(field => !field)) {
    return { task: null, error: '请按「任务类型|目标|截止步数」填写，三项不能为空。' }
  }

  const [typeText, target, deadlineText] = fields as [string, string, string]
  if (!taskTypes.includes(typeText as TaskType)) {
    return { task: null, error: `任务类型只能是：${taskTypes.join('、')}。` }
  }

  const type = typeText as TaskType
  if (!targetsByType[type].includes(target)) {
    return { task: null, error: `${type}的目标只能是：${targetsByType[type].join('、')}。` }
  }
  if (deadlineText !== '60') {
    return { task: null, error: '首版截止步数固定为 60，请填写整数 60。' }
  }

  return { task: { type, target, deadlineStep: 60 }, error: '' }
}

export function validateTaskPair(redInput: string, blueInput: string): { tasks: TaskPair | null; errors: TaskErrors } {
  const redResult = parseTaskLine(redInput)
  const blueResult = parseTaskLine(blueInput)
  const errors: TaskErrors = { red: redResult.error, blue: blueResult.error, shared: '' }
  if (!redResult.task || !blueResult.task) return { tasks: null, errors }

  if (redResult.task.type !== blueResult.task.type) {
    errors.shared = '红蓝双方必须选择同一种任务类型，以保持对局可比。'
  } else if (
    (redResult.task.type === '区域巡查' || redResult.task.type === '物资护送') &&
    redResult.task.target === blueResult.task.target
  ) {
    errors.shared = '巡查或护送任务须采用对称路线：一方 A-B-C，另一方 C-B-A。'
  }

  if (errors.shared) return { tasks: null, errors }
  return { tasks: { red: redResult.task, blue: blueResult.task }, errors }
}

export function taskTargetLabel(target: string): string {
  if (target === 'A-B-C') return '区域 A → B → C'
  if (target === 'C-B-A') return '区域 C → B → A'
  return target
}

export function taskNodeLabels(task: SideTask): string[] {
  if (task.type === '区域巡查') return task.target.split('-').map(area => `区域 ${area}`)
  if (task.type === '物资护送') return task.target.split('-').map(area => `检查点 ${area}`)
  if (task.type === '关键设施保护') return ['第 20 步', '第 40 步', '第 60 步']
  return ['完成定位', '接近设备', '到达撤离点']
}

export function taskTitle(type: TaskType): string {
  return type === '区域巡查' ? '完成区域巡查' :
    type === '物资护送' ? '完成物资护送' :
      type === '关键设施保护' ? '保护关键设施' : '撤离受损设备'
}
