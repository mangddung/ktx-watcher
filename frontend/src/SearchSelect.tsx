import { useRef, useState } from 'react'
import { Input, Select, type RefSelectProps, type SelectProps } from 'antd'
import type { DefaultOptionType } from 'antd/es/select'
import { SearchOutlined } from '@ant-design/icons'

// 모바일에서 드롭다운을 열거나 항목을 고를 때 키보드가 뜨지 않도록,
// Select 자체 검색 입력 대신 드롭다운 안에 검색칸을 둔다. 키보드는 검색칸을 누를 때만 뜬다.
export default function SearchSelect({ options = [], match, searchPlaceholder = '검색', onOpenChange, onChange, ...rest }: SelectProps & {
  match: (option: DefaultOptionType, query: string) => boolean
  searchPlaceholder?: string
}) {
  const [query, setQuery] = useState('')
  const ref = useRef<RefSelectProps>(null)
  const q = query.trim()

  return (
    <Select
      {...rest}
      ref={ref}
      showSearch={false}
      onChange={(value, option) => {
        // 검색칸에 포커스가 남아 있으면 모바일 키보드가 계속 떠 있으므로, 고르는 즉시
        // 읽기 전용인 Select 입력으로 포커스를 돌린다(키보드는 내려가고 다중 선택 드롭다운은 열린 채 유지)
        if (document.activeElement instanceof HTMLElement && document.activeElement.closest('.ant-select-dropdown'))
          ref.current?.focus()
        onChange?.(value, option)
      }}
      options={q ? options.filter((o) => match(o, q)) : options}
      onOpenChange={(open) => {
        if (!open) setQuery('')
        onOpenChange?.(open)
      }}
      popupRender={(menu) => (
        <>
          <div style={{ padding: 4, paddingBottom: 8 }}>
            <Input
              allowClear
              size="middle"
              prefix={<SearchOutlined />}
              placeholder={searchPlaceholder}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.stopPropagation()}
              aria-label={searchPlaceholder}
            />
          </div>
          {menu}
        </>
      )}
    />
  )
}
