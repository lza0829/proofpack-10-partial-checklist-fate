// 包状态 / 清单项命运字典：前后端口径单一来源
export const PACK_STATUS = {
  built: '已生成',
  checked_ok: '校验通过',
  partial_rejected: '部分失败·整包驳回',
  partial_suspended: '部分失败·挂起待清',
  released: '已放行',
  rejected: '已驳回',
}

export const ITEM_STATE = {
  passed: '通过',
  failed: '未通过',
  suspended: '挂起',
}

export const POLICY = {
  reject_all: '整包驳回口径',
  suspend_items: '分项挂起',
}

export function packStatusLabel(status) {
  return PACK_STATUS[status] || status
}

export function itemStateLabel(state) {
  return ITEM_STATE[state] || state
}
