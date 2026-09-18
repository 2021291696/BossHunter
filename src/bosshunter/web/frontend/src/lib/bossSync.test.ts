import { describe, expect, it } from 'vitest'

import { buildSyncFromBoss, normalizeCityName } from './bossSync'

const zhilianOptions = [
  { name: '北京', code: '530' },
  { name: '成都', code: '801' },
  { name: '杭州市', code: '653' },
]

describe('normalizeCityName', () => {
  it('strips trailing 市 and whitespace', () => {
    expect(normalizeCityName(' 杭州市 ')).toBe('杭州')
    expect(normalizeCityName('成都')).toBe('成都')
  })
})

describe('buildSyncFromBoss', () => {
  it('returns null when BOSS keywords are empty', () => {
    expect(buildSyncFromBoss({ keywords: [], cities: ['北京'] }, zhilianOptions)).toBeNull()
    expect(buildSyncFromBoss({}, zhilianOptions)).toBeNull()
  })

  it('syncs keywords, matched cities with codes and max_pages', () => {
    const outcome = buildSyncFromBoss(
      { keywords: ['数据分析', ' 产品 '], cities: ['北京', '成都'], max_pages: 5 },
      zhilianOptions,
    )
    expect(outcome).toEqual({
      keywords: ['数据分析', '产品'],
      cities: ['北京', '成都'],
      city_codes: { 北京: '530', 成都: '801' },
      max_pages: 5,
      skippedCities: [],
    })
  })

  it('skips cities missing from the target catalog and keeps the rest', () => {
    const outcome = buildSyncFromBoss(
      { keywords: ['数据分析'], cities: ['北京', '攀枝花'], max_pages: 2 },
      zhilianOptions,
    )
    expect(outcome?.cities).toEqual(['北京'])
    expect(outcome?.city_codes).toEqual({ 北京: '530' })
    expect(outcome?.skippedCities).toEqual(['攀枝花'])
  })

  it('falls back to default max_pages when boss value is invalid', () => {
    const outcome = buildSyncFromBoss({ keywords: ['数据分析'], cities: [], max_pages: 0 }, zhilianOptions)
    expect(outcome?.max_pages).toBe(3)
  })

  it('matches catalog names carrying the 市 suffix', () => {
    const outcome = buildSyncFromBoss({ keywords: ['产品'], cities: ['杭州'] }, zhilianOptions)
    expect(outcome?.city_codes).toEqual({ 杭州: '653' })
    expect(outcome?.skippedCities).toEqual([])
  })
})
