import React, { useState, useRef, useEffect, useMemo } from 'react'
import {
  Box, Paper, Typography, TextField, IconButton, Button, Chip,
  CircularProgress, Collapse, Select, MenuItem, Tooltip, Divider,
} from '@mui/material'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import SendIcon from '@mui/icons-material/Send'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep'
import BuildCircleOutlinedIcon from '@mui/icons-material/BuildCircleOutlined'
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import SmartToyOutlinedIcon from '@mui/icons-material/SmartToyOutlined'
import PersonOutlineIcon from '@mui/icons-material/PersonOutline'

const TOOL_LABELS = {
  advise_all: 'Ran All Capacity Advisories (Calendar + Spot + Flex)',
  catalog_chips_and_machines: 'Checked GPU/TPU Catalog',
  advise_spot: 'Ran Spot VM Advisory',
  advise_calendar: 'Ran DWS Calendar Advisory',
  advise_flex: 'Ran DWS Flex Advisory',
  check_project_quotas: 'Checked Project Quotas',
  list_project_networks: 'Checked VPC Networks',
  drive_ui_configure_scan: 'Configured Scan & Deploy Tab',
  drive_ui_filter_quotas: 'Filtered Quota Tab',
  drive_ui_tab: 'Switched Tab',
}

const MODELS = [
  { id: 'gemini-3-flash-preview', label: 'Gemini 3 Flash' },
  { id: 'gemini-3.1-pro-preview', label: 'Gemini 3.1 Pro' },
]

/**
 * Lightweight markdown renderer for assistant messages (supports headings, bold,
 * inline code, fenced code blocks, markdown links, bullet lists, and tables).
 */
function renderInline(text) {
  if (!text) return null
  // Tokenize links [label](url), inline code `code`, and bold **text**
  const pattern = /(\[[^\]]+\]\(https?:\/\/[^\s)]+\)|`[^`]+`|\*\*[^*]+\*\*)/g
  const parts = String(text).split(pattern)
  return parts.map((part, idx) => {
    if (!part) return null
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return (
        <code
          key={idx}
          style={{
            background: '#f1f3f4',
            color: '#c5221f',
            padding: '1px 5px',
            borderRadius: 4,
            fontSize: '0.78rem',
            fontFamily: 'monospace',
          }}
        >
          {part.slice(1, -1)}
        </code>
      )
    }
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={idx}>{part.slice(2, -2)}</strong>
    }
    const linkMatch = part.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/)
    if (linkMatch) {
      return (
        <a
          key={idx}
          href={linkMatch[2]}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: '#1a73e8', textDecoration: 'none', fontWeight: 500 }}
        >
          {linkMatch[1]} <OpenInNewIcon sx={{ fontSize: 11, verticalAlign: 'middle' }} />
        </a>
      )
    }
    return part
  })
}

function FormattedMessage({ content }) {
  if (!content) return null
  const lines = String(content).split('\n')
  const elements = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    // Fenced code block
    if (line.trim().startsWith('```')) {
      const codeLines = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        codeLines.push(lines[i])
        i++
      }
      i++ // skip closing ```
      elements.push(
        <Box
          key={`code-${i}`}
          component="pre"
          sx={{
            bgcolor: '#202124',
            color: '#e8eaed',
            p: 1.25,
            borderRadius: 1,
            fontSize: '0.75rem',
            overflowX: 'auto',
            my: 0.75,
            fontFamily: 'monospace',
          }}
        >
          {codeLines.join('\n')}
        </Box>
      )
      continue
    }

    // Markdown table
    if (
      line.includes('|') &&
      i + 1 < lines.length &&
      /^\s*\|?[\s:-]+\|[\s|:-]*$/.test(lines[i + 1])
    ) {
      const parseRow = (r) =>
        r
          .trim()
          .replace(/^\|/, '')
          .replace(/\|$/, '')
          .split('|')
          .map((c) => c.trim())
      const headers = parseRow(line)
      i += 2 // skip header and separator
      const rows = []
      while (i < lines.length && lines[i].includes('|') && lines[i].trim() !== '') {
        rows.push(parseRow(lines[i]))
        i++
      }
      elements.push(
        <Box key={`tbl-${i}`} sx={{ overflowX: 'auto', my: 1 }}>
          <table
            style={{
              borderCollapse: 'collapse',
              width: '100%',
              fontSize: '0.76rem',
              border: '1px solid #dadce0',
            }}
          >
            <thead>
              <tr style={{ background: '#f8f9fa' }}>
                {headers.map((h, hi) => (
                  <th
                    key={hi}
                    style={{
                      textAlign: 'left',
                      padding: '5px 8px',
                      borderBottom: '1px solid #dadce0',
                      fontWeight: 600,
                      color: '#3c4043',
                    }}
                  >
                    {renderInline(h)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} style={{ borderBottom: '1px solid #f1f3f4' }}>
                  {r.map((cell, ci) => (
                    <td key={ci} style={{ padding: '4px 8px', color: '#3c4043' }}>
                      {renderInline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </Box>
      )
      continue
    }

    // Headings
    if (line.startsWith('### ')) {
      elements.push(
        <Typography key={i} variant="subtitle2" sx={{ fontWeight: 700, mt: 1, mb: 0.25, fontSize: '0.84rem', color: '#202124' }}>
          {renderInline(line.slice(4))}
        </Typography>
      )
      i++
      continue
    }
    if (line.startsWith('## ') || line.startsWith('# ')) {
      const text = line.replace(/^#+\s*/, '')
      elements.push(
        <Typography key={i} variant="subtitle2" sx={{ fontWeight: 700, mt: 1, mb: 0.3, fontSize: '0.88rem', color: '#1a73e8' }}>
          {renderInline(text)}
        </Typography>
      )
      i++
      continue
    }

    // Bullet list items
    if (/^\s*[-*]\s+/.test(line)) {
      const items = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ''))
        i++
      }
      elements.push(
        <Box key={`ul-${i}`} component="ul" sx={{ m: 0, pl: 2.2, my: 0.4 }}>
          {items.map((it, idx) => (
            <li key={idx} style={{ marginBottom: 2, fontSize: '0.82rem', lineHeight: 1.45 }}>
              {renderInline(it)}
            </li>
          ))}
        </Box>
      )
      continue
    }

    // Numbered list items
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = []
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ''))
        i++
      }
      elements.push(
        <Box key={`ol-${i}`} component="ol" sx={{ m: 0, pl: 2.4, my: 0.4 }}>
          {items.map((it, idx) => (
            <li key={idx} style={{ marginBottom: 2, fontSize: '0.82rem', lineHeight: 1.45 }}>
              {renderInline(it)}
            </li>
          ))}
        </Box>
      )
      continue
    }

    // Empty line
    if (!line.trim()) {
      elements.push(<Box key={i} sx={{ height: 4 }} />)
      i++
      continue
    }

    // Normal paragraph
    elements.push(
      <Typography key={i} variant="body2" sx={{ fontSize: '0.82rem', lineHeight: 1.5, mb: 0.3 }}>
        {renderInline(line)}
      </Typography>
    )
    i++
  }

  return <Box>{elements}</Box>
}

export default function ChatAssistant({
  open,
  onToggle,
  project = '',
  userToken = '',
  activeTab = 0,
  uiContext = {},
  onUiAction,
}) {
  const [model, setModel] = useState('gemini-3-flash-preview')
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content:
        "Hi! I'm your **Capacity Radar AI Assistant** (powered by the Capacity Radar Agent Skill). Ask me about the results visible on the tab below, or ask me to check capacity, inspect quotas, or configure a multi-priority scan plan for you.",
      toolCalls: [],
    },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const scrollRef = useRef(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, loading, open])

  // Contextual badge & quick prompts based on which tab is open below
  const contextBadge = useMemo(() => {
    if (activeTab === 0) {
      const mt = uiContext?.advisory?.machineType
      return mt ? `Advisory · ${mt}` : 'Viewing: Capacity Advisory'
    }
    if (activeTab === 1) {
      const mt = uiContext?.scan?.machineType
      const st = uiContext?.scan?.status
      return mt ? `Scan & Deploy · ${mt}${st ? ` (${st})` : ''}` : 'Viewing: Scan & Deploy'
    }
    if (activeTab === 2) {
      const count = uiContext?.quota?.filteredCount
      return count != null ? `Quota · ${count} quotas shown` : 'Viewing: Quota Explorer'
    }
    return 'Capacity Radar'
  }, [activeTab, uiContext])

  const quickPrompts = useMemo(() => {
    if (activeTab === 0) {
      const mt = uiContext?.advisory?.machineType || 'a3-highgpu-8g'
      return [
        `Explain the advisory results on screen`,
        `Where can I get Spot capacity for ${mt} right now?`,
        `Check DWS Calendar availability for 2x ${mt} next week`,
        `Which quota do I need for ${mt}?`,
      ]
    }
    if (activeTab === 1) {
      const mt = uiContext?.scan?.machineType || 'a3-highgpu-8g'
      return [
        `Build a Spot → DWS Flex → Calendar scan plan for ${mt}`,
        `Explain the scan status and logs below`,
        `Check if my project has enough quota for this scan`,
      ]
    }
    return [
      `Which quotas on my project currently have a limit > 0?`,
      `What quota do I need for H100s on DWS Calendar vs Spot?`,
      `Show me all TPU v6e quotas on the tab below`,
      `Find me 1x L4 GPU on Spot and set up a scan`,
    ]
  }, [activeTab, uiContext])

  const sendMessage = async (textOverride) => {
    const text = (textOverride ?? input).trim()
    if (!text || loading) return

    const nextMessages = [...messages, { role: 'user', content: text }]
    setMessages(nextMessages)
    if (textOverride === undefined) setInput('')
    setLoading(true)

    try {
      const headers = { 'Content-Type': 'application/json' }
      if (userToken) headers['X-User-Token'] = userToken

      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          project,
          model,
          messages: nextMessages.map((m) => ({ role: m.role, content: m.content })),
          uiContext: {
            activeTab: activeTab === 0 ? 'advisory' : activeTab === 1 ? 'scan' : 'quota',
            ...uiContext,
          },
        }),
      })

      const data = await resp.json()
      if (!resp.ok) {
        throw new Error(data.detail || `HTTP ${resp.status}`)
      }

      // Apply any UI-driving actions emitted by the agent
      if (Array.isArray(data.uiActions) && data.uiActions.length > 0 && onUiAction) {
        data.uiActions.forEach((act) => onUiAction(act))
      }

      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: data.reply || 'Done.',
          toolCalls: data.toolCalls || [],
          uiActions: data.uiActions || [],
          model: data.model || model,
        },
      ])
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: `**Error:** ${err.message || 'Failed to reach AI Assistant.'}`,
          toolCalls: [],
          isError: true,
        },
      ])
    } finally {
      setLoading(false)
    }
  }

  const handleClear = () => {
    setMessages([
      {
        role: 'assistant',
        content:
          'Chat cleared. Ask me about what you see on the tab below or tell me what GPU/TPU capacity you want to find!',
        toolCalls: [],
      },
    ])
  }

  return (
    <Paper
      elevation={0}
      sx={{
        borderBottom: '1px solid #dadce0',
        bgcolor: open ? '#f8fafd' : '#ffffff',
        transition: 'background-color 0.2s',
      }}
    >
      {/* Persistent Top Assistant Bar */}
      <Box
        sx={{
          px: 3,
          py: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 1.5,
          cursor: 'pointer',
          '&:hover': { bgcolor: '#f1f5fb' },
        }}
        onClick={onToggle}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.2, minWidth: 0 }}>
          <Box
            sx={{
              width: 28,
              height: 28,
              borderRadius: '50%',
              background: 'linear-gradient(135deg, #1a73e8 0%, #9334e6 100%)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#fff',
              flexShrink: 0,
            }}
          >
            <AutoAwesomeIcon sx={{ fontSize: 16 }} />
          </Box>
          <Typography variant="subtitle2" sx={{ fontWeight: 700, color: '#1a73e8', fontSize: '0.85rem' }}>
            AI Capacity Assistant
          </Typography>
          <Chip
            size="small"
            label={contextBadge}
            sx={{
              height: 20,
              fontSize: '0.68rem',
              bgcolor: '#e8f0fe',
              color: '#1967d2',
              fontWeight: 600,
            }}
          />
          {!open && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{
                display: { xs: 'none', md: 'inline' },
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              Ask questions about visible tab results or let the agent find capacity &amp; drive the UI
            </Typography>
          )}
        </Box>

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }} onClick={(e) => e.stopPropagation()}>
          {open && (
            <>
              <Select
                size="small"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                sx={{
                  height: 26,
                  fontSize: '0.72rem',
                  bgcolor: '#fff',
                  '& .MuiSelect-select': { py: 0.25, px: 1 },
                }}
              >
                {MODELS.map((m) => (
                  <MenuItem key={m.id} value={m.id} sx={{ fontSize: '0.75rem' }}>
                    {m.label}
                  </MenuItem>
                ))}
              </Select>
              <Tooltip title="Clear chat history">
                <IconButton size="small" onClick={handleClear} sx={{ p: 0.4 }}>
                  <DeleteSweepIcon sx={{ fontSize: 17, color: '#5f6368' }} />
                </IconButton>
              </Tooltip>
            </>
          )}
          <Button
            size="small"
            variant={open ? 'outlined' : 'contained'}
            onClick={onToggle}
            endIcon={open ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            sx={{
              textTransform: 'none',
              fontSize: '0.74rem',
              py: 0.25,
              px: 1.25,
              minHeight: 26,
              ...(open
                ? { borderColor: '#dadce0', color: '#3c4043' }
                : {
                    background: 'linear-gradient(135deg, #1a73e8 0%, #7b1fa2 100%)',
                    color: '#fff',
                  }),
            }}
          >
            {open ? 'Hide Chat' : 'Open Chat'}
          </Button>
        </Box>
      </Box>

      {/* Expandable Chat Drawer Content */}
      <Collapse in={open}>
        <Divider />
        <Box sx={{ px: 3, py: 1.5, maxWidth: 1400, mx: 'auto' }}>
          {/* Messages Scroll Area */}
          <Box
            ref={scrollRef}
            sx={{
              maxHeight: 320,
              minHeight: 160,
              overflowY: 'auto',
              pr: 1,
              mb: 1.25,
              display: 'flex',
              flexDirection: 'column',
              gap: 1.25,
            }}
          >
            {messages.map((m, idx) => {
              const isUser = m.role === 'user'
              return (
                <Box
                  key={idx}
                  sx={{
                    display: 'flex',
                    justifyContent: isUser ? 'flex-end' : 'flex-start',
                    alignItems: 'flex-start',
                    gap: 1,
                  }}
                >
                  {!isUser && (
                    <Box
                      sx={{
                        width: 24,
                        height: 24,
                        borderRadius: '50%',
                        bgcolor: '#e8f0fe',
                        color: '#1a73e8',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        mt: 0.3,
                        flexShrink: 0,
                      }}
                    >
                      <SmartToyOutlinedIcon sx={{ fontSize: 15 }} />
                    </Box>
                  )}

                  <Box
                    sx={{
                      maxWidth: '85%',
                      bgcolor: isUser ? '#1a73e8' : '#ffffff',
                      color: isUser ? '#ffffff' : '#202124',
                      border: isUser ? 'none' : '1px solid #e0e3e7',
                      borderRadius: 2,
                      px: 1.75,
                      py: 1,
                      boxShadow: isUser ? 'none' : '0 1px 2px rgba(0,0,0,0.03)',
                    }}
                  >
                    {/* Tool Execution Trace Pills */}
                    {!isUser && Array.isArray(m.toolCalls) && m.toolCalls.length > 0 && (
                      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mb: 0.75 }}>
                        {m.toolCalls.map((tc, tci) => {
                          const label = TOOL_LABELS[tc.name] || tc.name
                          const detail =
                            tc.args?.machine_type ||
                            tc.args?.chip ||
                            tc.args?.chip_filter ||
                            tc.args?.tab ||
                            ''
                          return (
                            <Chip
                              key={tci}
                              size="small"
                              icon={
                                tc.ok ? (
                                  <CheckCircleOutlineIcon sx={{ fontSize: '13px !important', color: '#137333 !important' }} />
                                ) : (
                                  <BuildCircleOutlinedIcon sx={{ fontSize: '13px !important' }} />
                                )
                              }
                              label={detail ? `${label}: ${detail}` : label}
                              sx={{
                                height: 20,
                                fontSize: '0.67rem',
                                bgcolor: '#e6f4ea',
                                color: '#137333',
                                fontWeight: 600,
                              }}
                            />
                          )
                        })}
                      </Box>
                    )}

                    {isUser ? (
                      <Typography variant="body2" sx={{ fontSize: '0.82rem', whiteSpace: 'pre-wrap' }}>
                        {m.content}
                      </Typography>
                    ) : (
                      <FormattedMessage content={m.content} />
                    )}
                  </Box>

                  {isUser && (
                    <Box
                      sx={{
                        width: 24,
                        height: 24,
                        borderRadius: '50%',
                        bgcolor: '#e8eaed',
                        color: '#5f6368',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        mt: 0.3,
                        flexShrink: 0,
                      }}
                    >
                      <PersonOutlineIcon sx={{ fontSize: 15 }} />
                    </Box>
                  )}
                </Box>
              )
            })}

            {loading && (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, pl: 0.5 }}>
                <CircularProgress size={16} />
                <Typography variant="caption" color="text.secondary">
                  Analyzing screen context &amp; querying capacity tools ({model})...
                </Typography>
              </Box>
            )}
          </Box>

          {/* Contextual Quick Prompts */}
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.6, mb: 1 }}>
            {quickPrompts.map((qp, idx) => (
              <Chip
                key={idx}
                label={qp}
                size="small"
                variant="outlined"
                onClick={() => sendMessage(qp)}
                disabled={loading}
                sx={{
                  fontSize: '0.7rem',
                  height: 22,
                  borderColor: '#d2e3fc',
                  bgcolor: '#fff',
                  color: '#1967d2',
                  cursor: 'pointer',
                  '&:hover': { bgcolor: '#e8f0fe', borderColor: '#1a73e8' },
                }}
              />
            ))}
          </Box>

          {/* Input Row */}
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
            <TextField
              fullWidth
              size="small"
              placeholder="Ask about the results below, or tell the agent what GPU/TPU capacity to find & configure..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={loading}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  sendMessage()
                }
              }}
              sx={{
                bgcolor: '#fff',
                '& .MuiInputBase-input': { fontSize: '0.82rem', py: 0.9 },
              }}
            />
            <Button
              variant="contained"
              onClick={() => sendMessage()}
              disabled={!input.trim() || loading}
              endIcon={<SendIcon sx={{ fontSize: 15 }} />}
              sx={{
                textTransform: 'none',
                fontWeight: 600,
                px: 2,
                py: 0.8,
                fontSize: '0.8rem',
                background: 'linear-gradient(135deg, #1a73e8 0%, #7b1fa2 100%)',
              }}
            >
              Ask
            </Button>
          </Box>
        </Box>
      </Collapse>
    </Paper>
  )
}
