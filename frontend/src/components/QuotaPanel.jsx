import React, { useState, useEffect, useMemo, useCallback } from 'react'
import {
  Box, Paper, Typography, Chip, Button, IconButton, Tooltip,
  TextField, InputAdornment, CircularProgress, Alert, Switch,
  FormControlLabel, ToggleButton, ToggleButtonGroup, Autocomplete,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  Collapse, Divider,
} from '@mui/material'
import SearchIcon from '@mui/icons-material/Search'
import RefreshIcon from '@mui/icons-material/Refresh'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import MemoryIcon from '@mui/icons-material/Memory'
import DeveloperBoardIcon from '@mui/icons-material/DeveloperBoard'
import DnsIcon from '@mui/icons-material/Dns'
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline'
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import FilterAltOffIcon from '@mui/icons-material/FilterAltOff'

const CONSUMPTION_META = {
  on_demand: { label: 'On-Demand / Reserved', color: '#1a73e8', bg: '#e8f0fe' },
  spot: { label: 'Spot VM', color: '#e37400', bg: '#fef7e0' },
  dws_flex: { label: 'DWS Flex-Start', color: '#9334e6', bg: '#f3e8fd' },
  dws_calendar: { label: 'DWS Calendar / Reserved', color: '#137333', bg: '#e6f4ea' },
  committed: { label: 'Committed (CUD)', color: '#5f6368', bg: '#f1f3f4' },
}

export default function QuotaPanel({ project, userToken, quotaPreset = null, onContextChange }) {
  const [quotas, setQuotas] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [apiWarnings, setApiWarnings] = useState([])

  // Filters
  const [categoryFilter, setCategoryFilter] = useState('ALL') // ALL | GPU | TPU
  const [selectedChips, setSelectedChips] = useState([])
  const [selectedFamilies, setSelectedFamilies] = useState([])
  const [selectedModels, setSelectedModels] = useState([])
  const [locationFilter, setLocationFilter] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [showVmQuotas, setShowVmQuotas] = useState(false)
  const [onlyNonZero, setOnlyNonZero] = useState(false)

  // Track which quota rows have expanded location lists (for 0-limit locations)
  const [expandedRows, setExpandedRows] = useState({})
  const [copiedId, setCopiedId] = useState(null)

  const fetchQuotas = useCallback(async (forceRefresh = false) => {
    if (!project) return
    setLoading(true)
    setError(null)
    try {
      const headers = {}
      if (userToken) {
        headers['X-User-Token'] = userToken
      }
      const url = `/api/quotas?project=${encodeURIComponent(project)}${forceRefresh ? '&refresh=true' : ''}`
      const resp = await fetch(url, { headers })
      const data = await resp.json()
      if (!resp.ok) {
        throw new Error(data.detail || `HTTP ${resp.status}`)
      }
      setQuotas(data.quotas || [])
      setApiWarnings(data.errors || [])
    } catch (err) {
      setError(err.message || 'Failed to load project quotas.')
    } finally {
      setLoading(false)
    }
  }, [project, userToken])

  useEffect(() => {
    if (project) {
      fetchQuotas(false)
    }
  }, [project, fetchQuotas])

  // Apply agent-driven filter preset when the AI Assistant filters the Quota tab
  useEffect(() => {
    if (!quotaPreset) return
    if (quotaPreset.category) setCategoryFilter(quotaPreset.category)
    if (Array.isArray(quotaPreset.chips)) setSelectedChips(quotaPreset.chips)
    if (Array.isArray(quotaPreset.families)) setSelectedFamilies(quotaPreset.families)
    if (Array.isArray(quotaPreset.models)) setSelectedModels(quotaPreset.models)
    if (quotaPreset.location !== undefined) setLocationFilter(quotaPreset.location || '')
    if (quotaPreset.search !== undefined) setSearchQuery(quotaPreset.search || '')
    if (typeof quotaPreset.showVmQuotas === 'boolean') setShowVmQuotas(quotaPreset.showVmQuotas)
    if (typeof quotaPreset.onlyNonZero === 'boolean') setOnlyNonZero(quotaPreset.onlyNonZero)
  }, [quotaPreset])

  // Distinct filter options derived from catalog
  const availableChips = useMemo(() => {
    const set = new Set()
    quotas.forEach(q => {
      if (categoryFilter !== 'ALL' && q.category !== categoryFilter) return
      if (!showVmQuotas && q.isVmQuota) return
      if (q.chip) set.add(q.chip)
    })
    return Array.from(set)
  }, [quotas, categoryFilter, showVmQuotas])

  const availableFamilies = useMemo(() => {
    const set = new Set()
    quotas.forEach(q => {
      if (categoryFilter !== 'ALL' && q.category !== categoryFilter) return
      if (!showVmQuotas && q.isVmQuota) return
      if (selectedChips.length > 0 && !selectedChips.includes(q.chip)) return
      ;(q.vmFamilies || []).forEach(f => set.add(f))
    })
    return Array.from(set).sort()
  }, [quotas, categoryFilter, showVmQuotas, selectedChips])

  const availableLocations = useMemo(() => {
    const set = new Set()
    quotas.forEach(q => {
      ;(q.locations || []).forEach(l => {
        if (l.location) set.add(l.location)
      })
    })
    return Array.from(set).sort()
  }, [quotas])

  const vmQuotaCount = useMemo(
    () => quotas.filter(q => q.isVmQuota && (categoryFilter === 'ALL' || q.category === categoryFilter)).length,
    [quotas, categoryFilter]
  )

  const toggleModelFilter = (modelKey) => {
    setSelectedModels(prev =>
      prev.includes(modelKey) ? prev.filter(m => m !== modelKey) : [...prev, modelKey]
    )
  }

  const clearAllFilters = () => {
    setCategoryFilter('ALL')
    setSelectedChips([])
    setSelectedFamilies([])
    setSelectedModels([])
    setLocationFilter('')
    setSearchQuery('')
    setOnlyNonZero(false)
  }

  const filteredQuotas = useMemo(() => {
    const qLower = searchQuery.trim().toLowerCase()
    const locLower = locationFilter.trim().toLowerCase()

    return quotas.filter(q => {
      if (!showVmQuotas && q.isVmQuota) return false
      if (categoryFilter !== 'ALL' && q.category !== categoryFilter) return false
      if (selectedChips.length > 0 && !selectedChips.includes(q.chip)) return false
      if (selectedFamilies.length > 0) {
        const hasFam = (q.vmFamilies || []).some(f => selectedFamilies.includes(f))
        if (!hasFam) return false
      }
      if (selectedModels.length > 0) {
        const hasModel = (q.consumptionModels || []).some(m => selectedModels.includes(m))
        if (!hasModel) return false
      }
      if (onlyNonZero) {
        if (locLower) {
          const matchingLocs = (q.locations || []).filter(l => l.location.toLowerCase().includes(locLower))
          if (!matchingLocs.some(l => l.limit > 0 || l.unlimited)) return false
        } else if ((q.nonZeroLocationsCount || 0) === 0 && !q.isUnlimited) {
          return false
        }
      }
      if (locLower) {
        const hasLoc = (q.locations || []).some(l => l.location.toLowerCase().includes(locLower))
        if (!hasLoc && q.scope !== 'global') return false
      }
      if (qLower) {
        const haystack = [
          q.chip,
          q.hardwareLabel,
          q.displayName,
          q.metric,
          q.legacyMetric,
          q.notes,
          ...(q.vmFamilies || []),
          ...(q.machineTypes || []),
          q.dimensions ? JSON.stringify(q.dimensions) : '',
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
        if (!haystack.includes(qLower)) return false
      }
      return true
    })
  }, [
    quotas,
    showVmQuotas,
    categoryFilter,
    selectedChips,
    selectedFamilies,
    selectedModels,
    onlyNonZero,
    locationFilter,
    searchQuery,
  ])

  // Report live Quota tab state & visible quotas to the AI Assistant
  useEffect(() => {
    if (!onContextChange) return
    onContextChange({
      categoryFilter,
      selectedChips,
      selectedFamilies,
      selectedModels,
      locationFilter,
      searchQuery,
      showVmQuotas,
      onlyNonZero,
      totalLoaded: quotas.length,
      filteredCount: filteredQuotas.length,
      visibleQuotas: filteredQuotas.slice(0, 20).map((q) => ({
        id: q.id,
        displayName: q.displayName,
        chip: q.chip,
        consumptionModels: q.consumptionModels,
        maxLimit: q.maxLimit,
        nonZeroLocations: (q.locations || []).filter((l) => (l.limit || 0) > 0).slice(0, 8),
      })),
    })
  }, [
    onContextChange,
    categoryFilter,
    selectedChips,
    selectedFamilies,
    selectedModels,
    locationFilter,
    searchQuery,
    showVmQuotas,
    onlyNonZero,
    quotas,
    filteredQuotas,
  ])

  // Group filtered quotas by Category + Chip
  const groupedQuotas = useMemo(() => {
    const groups = []
    const map = new Map()
    for (const q of filteredQuotas) {
      const key = `${q.category}::${q.chip}`
      if (!map.has(key)) {
        const group = {
          key,
          category: q.category,
          chip: q.chip,
          hardwareLabel: q.hardwareLabel,
          items: [],
        }
        map.set(key, group)
        groups.push(group)
      }
      map.get(key).items.push(q)
    }
    return groups
  }, [filteredQuotas])

  const handleCopyMetric = (e, id, text) => {
    e.stopPropagation()
    navigator.clipboard.writeText(text)
    setCopiedId(id)
    setTimeout(() => setCopiedId(null), 1500)
  }

  const handleOpenConsole = (url, e) => {
    if (e) e.stopPropagation()
    if (!url) return
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  const toggleExpandRow = (e, id) => {
    e.stopPropagation()
    setExpandedRows(prev => ({ ...prev, [id]: !prev[id] }))
  }

  const hasActiveFilters =
    categoryFilter !== 'ALL' ||
    selectedChips.length > 0 ||
    selectedFamilies.length > 0 ||
    selectedModels.length > 0 ||
    Boolean(locationFilter) ||
    Boolean(searchQuery) ||
    onlyNonZero

  return (
    <Box>
      {/* Header Banner */}
      <Paper sx={{ p: 2.5, mb: 2.5, border: '1px solid #e8eaed', borderRadius: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: 2 }}>
          <Box>
            <Typography variant="h6" sx={{ fontWeight: 700, color: '#202124', display: 'flex', alignItems: 'center', gap: 1 }}>
              <MemoryIcon sx={{ color: '#1a73e8' }} />
              GPU &amp; TPU Quota Explorer
              {project && (
                <Chip
                  label={project}
                  size="small"
                  sx={{ ml: 1, bgcolor: '#e8f0fe', color: '#1a73e8', fontWeight: 600, fontSize: '0.75rem' }}
                />
              )}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              See every quota required for each GPU/TPU type, VM family, and consumption model, inspect live regional/zonal limits, and click any quota or location to open it directly in the Google Cloud Console.
            </Typography>
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Button
              variant="outlined"
              size="small"
              startIcon={loading ? <CircularProgress size={14} /> : <RefreshIcon />}
              onClick={() => fetchQuotas(true)}
              disabled={loading || !project}
              sx={{ textTransform: 'none' }}
            >
              {loading ? 'Refreshing...' : 'Refresh Live Quotas'}
            </Button>
            {project && (
              <Button
                variant="contained"
                size="small"
                endIcon={<OpenInNewIcon sx={{ fontSize: 15 }} />}
                onClick={() =>
                  handleOpenConsole(`https://console.cloud.google.com/iam-admin/quotas?project=${encodeURIComponent(project)}`)
                }
                sx={{ textTransform: 'none', bgcolor: '#1a73e8' }}
              >
                All Project Quotas
              </Button>
            )}
          </Box>
        </Box>

        {/* Filter Controls */}
        <Divider sx={{ my: 2 }} />

        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {/* Row 1: Category toggle + Search + Location + Toggles */}
          <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 1.5 }}>
            <ToggleButtonGroup
              value={categoryFilter}
              exclusive
              size="small"
              onChange={(_, val) => {
                if (val) {
                  setCategoryFilter(val)
                  setSelectedChips([])
                  setSelectedFamilies([])
                }
              }}
              sx={{
                '& .MuiToggleButton-root': {
                  textTransform: 'none',
                  px: 1.8,
                  py: 0.6,
                  fontSize: '0.8rem',
                  fontWeight: 600,
                },
              }}
            >
              <ToggleButton value="ALL">All (GPU + TPU)</ToggleButton>
              <ToggleButton value="GPU">
                <MemoryIcon sx={{ fontSize: 16, mr: 0.6 }} />
                GPUs Only
              </ToggleButton>
              <ToggleButton value="TPU">
                <DeveloperBoardIcon sx={{ fontSize: 16, mr: 0.6 }} />
                TPUs Only
              </ToggleButton>
            </ToggleButtonGroup>

            <TextField
              size="small"
              placeholder="Search metric, chip, machine type (e.g. ct6e, B200, gpus_per_gpu_family)..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              sx={{ minWidth: 290, flex: 1 }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon sx={{ fontSize: 18, color: '#5f6368' }} />
                  </InputAdornment>
                ),
              }}
            />

            <Autocomplete
              size="small"
              options={availableLocations}
              value={locationFilter || null}
              onChange={(_, val) => setLocationFilter(val || '')}
              renderInput={(params) => (
                <TextField {...params} label="Filter Region / Zone" placeholder="e.g. us-central1" />
              )}
              sx={{ minWidth: 200 }}
            />

            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={onlyNonZero}
                  onChange={(e) => setOnlyNonZero(e.target.checked)}
                  color="success"
                />
              }
              label={
                <Typography variant="caption" sx={{ fontWeight: 600, color: '#3c4043' }}>
                  Only Quota &gt; 0
                </Typography>
              }
              sx={{ ml: 0.5, mr: 0 }}
            />

            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={showVmQuotas}
                  onChange={(e) => setShowVmQuotas(e.target.checked)}
                  color="primary"
                />
              }
              label={
                <Tooltip title="Include companion host vCPU quotas (CPUS, PREEMPTIBLE_CPUS, A2_CPUS), TPU QueuedResource limits, and GCE TPU slice quotas">
                  <Typography variant="caption" sx={{ fontWeight: 600, color: '#3c4043', display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    <DnsIcon sx={{ fontSize: 15, color: '#5f6368' }} />
                    Related VM / Host Quotas ({vmQuotaCount})
                  </Typography>
                </Tooltip>
              }
              sx={{ mr: 0 }}
            />
          </Box>

          {/* Row 2: Multi-select GPU/TPU Chip & VM Family + Consumption Model Chips */}
          <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 1.5 }}>
            <Autocomplete
              multiple
              size="small"
              options={availableChips}
              value={selectedChips}
              onChange={(_, val) => setSelectedChips(val)}
              renderInput={(params) => (
                <TextField {...params} label="GPU / TPU Types" placeholder="All types" />
              )}
              sx={{ minWidth: 240, flex: 1 }}
            />

            <Autocomplete
              multiple
              size="small"
              options={availableFamilies}
              value={selectedFamilies}
              onChange={(_, val) => setSelectedFamilies(val)}
              renderInput={(params) => (
                <TextField {...params} label="VM Families" placeholder="All families" />
              )}
              sx={{ minWidth: 240, flex: 1 }}
            />

            <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 0.75 }}>
              <Typography variant="caption" sx={{ fontWeight: 600, color: '#5f6368', mr: 0.5 }}>
                Consumption:
              </Typography>
              {Object.entries(CONSUMPTION_META).map(([key, meta]) => {
                const active = selectedModels.includes(key)
                return (
                  <Chip
                    key={key}
                    label={meta.label}
                    size="small"
                    onClick={() => toggleModelFilter(key)}
                    sx={{
                      fontSize: '0.72rem',
                      fontWeight: 600,
                      cursor: 'pointer',
                      bgcolor: active ? meta.color : meta.bg,
                      color: active ? '#fff' : meta.color,
                      border: `1px solid ${meta.color}`,
                      '&:hover': {
                        bgcolor: active ? meta.color : meta.bg,
                        opacity: 0.9,
                      },
                    }}
                  />
                )
              })}
              {hasActiveFilters && (
                <Button
                  size="small"
                  startIcon={<FilterAltOffIcon sx={{ fontSize: 15 }} />}
                  onClick={clearAllFilters}
                  sx={{ textTransform: 'none', fontSize: '0.75rem', ml: 0.5 }}
                >
                  Reset
                </Button>
              )}
            </Box>
          </Box>
        </Box>
      </Paper>

      {/* Errors / Warnings */}
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      {apiWarnings.length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {apiWarnings.join(' • ')}
        </Alert>
      )}

      {/* Loading State */}
      {loading && quotas.length === 0 && (
        <Paper sx={{ p: 6, textAlign: 'center', border: '1px solid #e8eaed' }}>
          <CircularProgress size={36} sx={{ mb: 2 }} />
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            Loading GPU &amp; TPU quotas for {project}...
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Querying Compute Engine and Cloud TPU Service Usage quotas across all regions and zones.
          </Typography>
        </Paper>
      )}

      {/* Summary bar */}
      {!loading && filteredQuotas.length === 0 && (
        <Paper sx={{ p: 4, textAlign: 'center', border: '1px solid #e8eaed' }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, color: '#5f6368' }}>
            No quotas match the current filters.
          </Typography>
          <Button size="small" onClick={clearAllFilters} sx={{ mt: 1, textTransform: 'none' }}>
            Reset Filters
          </Button>
        </Paper>
      )}

      {/* Grouped Quota Tables */}
      {groupedQuotas.map((group) => (
        <Paper
          key={group.key}
          sx={{
            mb: 2.5,
            border: '1px solid #e0e0e0',
            borderRadius: 2,
            overflow: 'hidden',
          }}
        >
          {/* Group Header */}
          <Box
            sx={{
              px: 2.5,
              py: 1.25,
              bgcolor: group.category === 'TPU' ? '#f3e8fd' : '#e8f0fe',
              borderBottom: '1px solid #dadce0',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              flexWrap: 'wrap',
              gap: 1,
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Chip
                label={group.category}
                size="small"
                sx={{
                  height: 22,
                  fontWeight: 700,
                  fontSize: '0.7rem',
                  bgcolor: group.category === 'TPU' ? '#7627bb' : '#1a73e8',
                  color: '#fff',
                }}
              />
              <Typography variant="subtitle1" sx={{ fontWeight: 700, color: '#202124' }}>
                {group.chip}
              </Typography>
              <Typography variant="body2" sx={{ color: '#5f6368' }}>
                &mdash; {group.hardwareLabel}
              </Typography>
            </Box>
            <Typography variant="caption" sx={{ fontWeight: 600, color: '#5f6368' }}>
              {group.items.length} quota{group.items.length > 1 ? 's' : ''}
            </Typography>
          </Box>

          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow sx={{ bgcolor: '#f8f9fa' }}>
                  <TableCell sx={{ fontWeight: 700, width: '18%', fontSize: '0.75rem', color: '#3c4043' }}>
                    VM Family / Machine Types
                  </TableCell>
                  <TableCell sx={{ fontWeight: 700, width: '16%', fontSize: '0.75rem', color: '#3c4043' }}>
                    Consumption Model
                  </TableCell>
                  <TableCell sx={{ fontWeight: 700, width: '28%', fontSize: '0.75rem', color: '#3c4043' }}>
                    Quota Name &amp; Metric (Click to Open Console)
                  </TableCell>
                  <TableCell sx={{ fontWeight: 700, width: '30%', fontSize: '0.75rem', color: '#3c4043' }}>
                    Live Project Limits by Region / Zone
                  </TableCell>
                  <TableCell align="right" sx={{ fontWeight: 700, width: '8%', fontSize: '0.75rem', color: '#3c4043' }}>
                    Console
                  </TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {group.items.map((q) => {
                  const isExpanded = Boolean(expandedRows[q.id])
                  const locLower = locationFilter.trim().toLowerCase()
                  const allLocs = locLower
                    ? (q.locations || []).filter(l => l.location.toLowerCase().includes(locLower))
                    : (q.locations || [])

                  const nonZeroLocs = allLocs.filter(l => l.limit > 0 || l.unlimited)
                  const zeroLocs = allLocs.filter(l => l.limit === 0 && !l.unlimited)
                  const visibleLocs = isExpanded
                    ? allLocs
                    : (nonZeroLocs.length > 0 ? nonZeroLocs.slice(0, 8) : allLocs.slice(0, 5))
                  const hiddenCount = allLocs.length - visibleLocs.length

                  return (
                    <TableRow
                      key={q.id}
                      hover
                      onClick={() => handleOpenConsole(q.consoleUrl)}
                      sx={{
                        cursor: 'pointer',
                        verticalAlign: 'top',
                        '&:hover': { bgcolor: '#f8fbff !important' },
                      }}
                    >
                      {/* VM Family & Machine Types */}
                      <TableCell sx={{ py: 1.5 }}>
                        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mb: 0.5 }}>
                          {(q.vmFamilies || []).map(fam => (
                            <Chip
                              key={fam}
                              label={fam}
                              size="small"
                              sx={{
                                height: 20,
                                fontSize: '0.7rem',
                                fontWeight: 600,
                                bgcolor: '#f1f3f4',
                                color: '#202124',
                              }}
                            />
                          ))}
                          {q.isVmQuota && (
                            <Chip
                              label="Related VM / Host Quota"
                              size="small"
                              sx={{
                                height: 20,
                                fontSize: '0.65rem',
                                fontWeight: 600,
                                bgcolor: '#fef7e0',
                                color: '#b06000',
                              }}
                            />
                          )}
                        </Box>
                        {q.machineTypes?.length > 0 && (
                          <Typography variant="caption" sx={{ color: '#5f6368', display: 'block', fontSize: '0.7rem' }}>
                            {q.machineTypes.slice(0, 4).join(', ')}
                            {q.machineTypes.length > 4 ? ` +${q.machineTypes.length - 4} more` : ''}
                          </Typography>
                        )}
                      </TableCell>

                      {/* Consumption Models */}
                      <TableCell sx={{ py: 1.5 }}>
                        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                          {(q.consumptionModels || []).map(m => {
                            const meta = CONSUMPTION_META[m] || { label: m, color: '#5f6368', bg: '#f1f3f4' }
                            return (
                              <Chip
                                key={m}
                                label={meta.label}
                                size="small"
                                sx={{
                                  height: 22,
                                  fontSize: '0.68rem',
                                  fontWeight: 600,
                                  bgcolor: meta.bg,
                                  color: meta.color,
                                }}
                              />
                            )
                          })}
                        </Box>
                      </TableCell>

                      {/* Quota Display Name & Metric */}
                      <TableCell sx={{ py: 1.5 }}>
                        <Typography variant="body2" sx={{ fontWeight: 700, color: '#1a73e8', mb: 0.3 }}>
                          {q.displayName}
                        </Typography>
                        <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 0.5, mb: 0.5 }}>
                          <code
                            style={{
                              fontSize: '0.7rem',
                              background: '#f1f3f4',
                              padding: '2px 6px',
                              borderRadius: 4,
                              color: '#3c4043',
                            }}
                          >
                            {q.metric}
                          </code>
                          <Tooltip title={copiedId === q.id ? 'Copied!' : 'Copy metric ID'}>
                            <IconButton
                              size="small"
                              onClick={(e) => handleCopyMetric(e, q.id, q.metric)}
                              sx={{ p: 0.25 }}
                            >
                              <ContentCopyIcon sx={{ fontSize: 13, color: copiedId === q.id ? '#137333' : '#5f6368' }} />
                            </IconButton>
                          </Tooltip>
                          {q.dimensions &&
                            Object.entries(q.dimensions).map(([dk, dv]) => (
                              <Chip
                                key={dk}
                                label={`${dk}=${dv}`}
                                size="small"
                                sx={{
                                  height: 19,
                                  fontSize: '0.65rem',
                                  fontWeight: 700,
                                  bgcolor: '#e8f0fe',
                                  color: '#1967d2',
                                }}
                              />
                            ))}
                        </Box>
                        {q.notes && (
                          <Typography variant="caption" sx={{ color: '#5f6368', display: 'block', lineHeight: 1.35 }}>
                            {q.notes}
                          </Typography>
                        )}
                      </TableCell>

                      {/* Live Project Limits by Region / Zone */}
                      <TableCell sx={{ py: 1.5 }} onClick={(e) => e.stopPropagation()}>
                        {/* Summary badge */}
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.75, flexWrap: 'wrap' }}>
                          {q.isUnlimited ? (
                            <Chip
                              icon={<CheckCircleOutlineIcon sx={{ fontSize: 14 }} />}
                              label="Unlimited (Reservation-backed)"
                              size="small"
                              color="success"
                              variant="outlined"
                              sx={{ height: 22, fontSize: '0.7rem', fontWeight: 600 }}
                            />
                          ) : q.maxLimit > 0 ? (
                            <Chip
                              icon={<CheckCircleOutlineIcon sx={{ fontSize: 14 }} />}
                              label={`Max: ${q.maxLimit.toLocaleString()} ${q.unitLabel} • ${q.nonZeroLocationsCount} location${q.nonZeroLocationsCount === 1 ? '' : 's'} > 0`}
                              size="small"
                              color="success"
                              sx={{ height: 22, fontSize: '0.7rem', fontWeight: 600 }}
                            />
                          ) : (
                            <Chip
                              icon={<ErrorOutlineIcon sx={{ fontSize: 14 }} />}
                              label={`0 ${q.unitLabel} across all locations`}
                              size="small"
                              sx={{
                                height: 22,
                                fontSize: '0.7rem',
                                fontWeight: 600,
                                bgcolor: '#fce8e6',
                                color: '#c5221f',
                              }}
                            />
                          )}
                        </Box>

                        {/* Individual Clickable Location Chips */}
                        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                          {visibleLocs.map((loc) => {
                            const hasQuota = loc.limit > 0 || loc.unlimited
                            const labelText = loc.unlimited
                              ? `${loc.location}: Unlimited`
                              : loc.usage > 0
                              ? `${loc.location}: ${loc.usage}/${loc.limit}`
                              : `${loc.location}: ${loc.limit}`
                            return (
                              <Tooltip
                                key={loc.location}
                                title={`Click to open ${q.displayName} in ${loc.location} on Cloud Console`}
                              >
                                <Chip
                                  label={labelText}
                                  size="small"
                                  onClick={(e) => handleOpenConsole(loc.consoleUrl, e)}
                                  sx={{
                                    height: 22,
                                    fontSize: '0.68rem',
                                    fontWeight: hasQuota ? 700 : 500,
                                    cursor: 'pointer',
                                    bgcolor: hasQuota ? '#e6f4ea' : '#f1f3f4',
                                    color: hasQuota ? '#137333' : '#5f6368',
                                    border: hasQuota ? '1px solid #ceead6' : '1px solid #e0e0e0',
                                    '&:hover': {
                                      bgcolor: hasQuota ? '#ceead6' : '#e8eaed',
                                    },
                                  }}
                                />
                              </Tooltip>
                            )
                          })}

                          {(hiddenCount > 0 || isExpanded) && allLocs.length > visibleLocs.length && (
                            <Chip
                              icon={isExpanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                              label={isExpanded ? 'Show less' : `+${hiddenCount} more (${zeroLocs.length} with 0)`}
                              size="small"
                              onClick={(e) => toggleExpandRow(e, q.id)}
                              sx={{
                                height: 22,
                                fontSize: '0.68rem',
                                cursor: 'pointer',
                                bgcolor: '#fff',
                                border: '1px dashed #bdc1c6',
                                color: '#1a73e8',
                              }}
                            />
                          )}
                          {isExpanded && allLocs.length === visibleLocs.length && allLocs.length > 5 && (
                            <Chip
                              icon={<ExpandLessIcon />}
                              label="Show less"
                              size="small"
                              onClick={(e) => toggleExpandRow(e, q.id)}
                              sx={{
                                height: 22,
                                fontSize: '0.68rem',
                                cursor: 'pointer',
                                bgcolor: '#fff',
                                border: '1px dashed #bdc1c6',
                                color: '#1a73e8',
                              }}
                            />
                          )}
                        </Box>
                      </TableCell>

                      {/* Direct Console Link Button */}
                      <TableCell align="right" sx={{ py: 1.5 }}>
                        <Tooltip title="Open this quota in Google Cloud Console">
                          <Button
                            size="small"
                            variant="outlined"
                            endIcon={<OpenInNewIcon sx={{ fontSize: 13 }} />}
                            onClick={(e) => handleOpenConsole(q.consoleUrl, e)}
                            sx={{
                              textTransform: 'none',
                              fontSize: '0.72rem',
                              py: 0.3,
                              px: 1,
                              whiteSpace: 'nowrap',
                            }}
                          >
                            Request
                          </Button>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      ))}
    </Box>
  )
}
