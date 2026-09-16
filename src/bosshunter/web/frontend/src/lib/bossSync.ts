/**
 * 「从 BOSS 同步」搜索配置的纯逻辑：把 BOSS 平台的搜索关键词/城市/页数
 * 构造为目标平台的平台级 search 配置。城市逐个对照目标平台内置目录，
 * 未收录的城市跳过并回传，由调用方提示（不猜测编码，与采集端校验口径一致）。
 */

export interface PlatformSearchLike {
  keywords?: string[]
  cities?: string[]
  max_pages?: number
  sort?: string
  [key: string]: unknown
}

export interface CityOptionLike {
  name: string
  code: string
}

export interface BossSyncOutcome {
  keywords: string[]
  cities: string[]
  city_codes: Record<string, string>
  max_pages: number
  skippedCities: string[]
}

export function normalizeCityName(city: string): string {
  return String(city || '').trim().replace(/市$/, '')
}

export function buildSyncFromBoss(
  boss: PlatformSearchLike,
  cityOptions: CityOptionLike[],
): BossSyncOutcome | null {
  const keywords = Array.isArray(boss?.keywords) ? boss.keywords.map(k => String(k).trim()).filter(Boolean) : []
  if (!keywords.length) return null

  const cities = Array.isArray(boss?.cities) ? boss.cities.map(c => String(c).trim()).filter(Boolean) : []
  const city_codes: Record<string, string> = {}
  const syncedCities: string[] = []
  const skippedCities: string[] = []
  for (const city of cities) {
    const matched = cityOptions.find(option => normalizeCityName(option.name) === normalizeCityName(city))
    if (matched) {
      city_codes[city] = matched.code
      syncedCities.push(city)
    } else {
      skippedCities.push(city)
    }
  }

  return {
    keywords,
    cities: syncedCities,
    city_codes,
    max_pages: Number(boss?.max_pages) > 0 ? Number(boss.max_pages) : 3,
    skippedCities,
  }
}
