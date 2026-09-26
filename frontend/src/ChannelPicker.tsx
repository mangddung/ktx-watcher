import { useEffect, useState } from 'react'
import { Avatar, Checkbox, Flex, Radio, Select, Tag, Typography } from 'antd'
import SearchSelect from './SearchSelect'
import { api, useConfig, type Channel, type ChannelKind, type Member } from './api'

export interface ChannelPick {
  mode: 'auto' | 'manual'
  viewers: string[]
  channelIds: number[]
}

export const emptyPick: ChannelPick = { mode: 'auto', viewers: [], channelIds: [] }

export const KIND_LABEL: Record<ChannelKind, string> = {
  discord_bot: 'Discord 봇 채널',
  discord_webhook: 'Discord 웹훅',
  telegram: 'Telegram',
}

export const usable = (c: Channel) => !c.system && !c.broken && c.endedWatchId == null

export default function ChannelPicker({ value, onChange, channels }: {
  value: ChannelPick
  onChange: (v: ChannelPick) => void
  channels: Channel[]
}) {
  const config = useConfig()

  // 봇이 연결돼 있지 않으면 채널을 자동으로 만들 수 없으니 직접 선택으로 바꾼다
  useEffect(() => {
    if (!config.botReady && value.mode === 'auto') onChange({ ...value, mode: 'manual' })
  }, [config.botReady, value, onChange])

  const options = (Object.keys(KIND_LABEL) as ChannelKind[])
    .map((kind) => ({
      label: KIND_LABEL[kind],
      options: channels.filter((c) => c.kind === kind && usable(c)).map((c) => ({ value: c.id, label: c.name })),
    }))
    .filter((g) => g.options.length)

  return (
    <Flex vertical gap={12}>
      <Radio.Group
        block
        optionType="button"
        buttonStyle="solid"
        value={value.mode}
        onChange={(e) => onChange({ ...value, mode: e.target.value })}
        options={[
          { value: 'auto', label: '자동 생성', disabled: !config.botReady },
          { value: 'manual', label: '직접 선택' },
        ]}
      />
      {value.mode === 'auto' ? (
        <>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            봇이 이 감시 전용 채널을 만듭니다. 관리자 외에는 보이지 않고, 아래에서 고른 멤버에게만 읽기 권한을 줍니다. 관리자는 항상 볼 수 있어 목록에 없습니다.
          </Typography.Text>
          <ViewerSelect value={value.viewers} onChange={(viewers) => onChange({ ...value, viewers })} />
        </>
      ) : (
        <Select
          mode="multiple"
          placeholder="알림을 보낼 채널"
          value={value.channelIds}
          onChange={(channelIds) => onChange({ ...value, channelIds })}
          options={options}
        />
      )}
    </Flex>
  )
}

// 읽기 권한을 줄 서버 멤버 고르기. 감시 추가·재개와 상세 화면에서 같이 쓴다
export function ViewerSelect({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [members, setMembers] = useState<Member[] | null>(null)

  useEffect(() => {
    api.members().then(setMembers).catch(() => setMembers([]))
  }, [])

  return (
    <SearchSelect
      id="viewers"
      mode="multiple"
      placeholder="읽기 권한을 줄 멤버 선택"
      value={value}
      onChange={onChange}
      loading={members === null}
      notFoundContent={members?.length === 0 ? '멤버 목록이 없습니다 (봇의 SERVER MEMBERS INTENT 를 확인하세요)' : undefined}
      options={(members ?? []).map((m) => ({ value: m.id, label: m.name, username: m.username, member: m }))}
      match={(o, q) => `${o.label} ${o.username}`.toLowerCase().includes(q.toLowerCase())}
      searchPlaceholder="이름이나 사용자명 검색"
      menuItemSelectedIcon={null}
      maxTagCount="responsive"
      allowClear
      optionRender={(o) => (
        <Flex align="center" gap={10}>
          <Checkbox checked={value.includes(o.value as string)} style={{ pointerEvents: 'none' }} />
          <MemberAvatar m={o.data.member} size={28} />
          <div style={{ lineHeight: 1.25 }}>
            <div style={{ fontWeight: 600 }}>{o.data.label}</div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>@{o.data.username}</Typography.Text>
          </div>
        </Flex>
      )}
      tagRender={({ value: id, closable, onClose }) => {
        const m = members?.find((x) => x.id === id)
        if (!m) return <span />
        return (
          <Tag
            closable={closable}
            onClose={onClose}
            onMouseDown={(e) => e.preventDefault()}
            style={{ borderRadius: 999, display: 'inline-flex', alignItems: 'center', gap: 6, paddingInlineStart: 3, marginInlineEnd: 4 }}
          >
            <MemberAvatar m={m} size={20} />
            {m.name}
          </Tag>
        )
      }}
    />
  )
}

function MemberAvatar({ m, size }: { m: Member; size: number }) {
  return (
    <Avatar size={size} src={m.avatar ?? undefined} style={{ flex: 'none', fontSize: size * 0.45 }}>
      {m.name.slice(0, 1)}
    </Avatar>
  )
}
