import React, { useState, useRef, useEffect } from 'react';
import {
  Box,
  Paper,
  TextField,
  IconButton,
  Typography,
  Container,
  Card,
  CardContent,
  Avatar,
  Chip,
  Fab,
  Tooltip,
  LinearProgress,
} from '@mui/material';
import {
  Send as SendIcon,
  Person as PersonIcon,
  SmartToy as BotIcon,
  Clear as ClearIcon,
  ContentCopy as CopyIcon,
  Stop as StopIcon,
  Refresh as RefreshIcon,
} from '@mui/icons-material';
import { useAppContext, useAppActions, Message } from '../contexts/AppContext';
import { useSocket } from '../contexts/SocketContext';
import MessageRenderer from '../components/MessageRenderer';
import { STATUS_TOAST_MESSAGES } from '../constants/statusToast';

export default function ChatPage() {
  const [inputMessage, setInputMessage] = useState('');
  const { state } = useAppContext();
  const { clearMessages, showNotification, getCurrentMessages, getCurrentChat } = useAppActions();
  const { sendMessage, isConnected, isConnecting, reconnect, stopGeneration } = useSocket();
  
  // Получаем текущий чат и сообщения
  const currentChat = getCurrentChat();
  const messages = getCurrentMessages();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Автоскролл к последнему сообщению
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Фокус на поле ввода при загрузке
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSendMessage = () => {
    if (!inputMessage.trim() || (!isConnected && !isConnecting) || state.isLoading || !currentChat) {
      return;
    }

    sendMessage(inputMessage.trim(), currentChat.id);
    setInputMessage('');
    
    // Возвращаем фокус на поле ввода
    setTimeout(() => {
      inputRef.current?.focus();
    }, 10);
  };

  const handleKeyPress = (event: React.KeyboardEvent) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      handleSendMessage();
    }
  };

  const handleCopyMessage = async (content: string) => {
    try {
      await navigator.clipboard.writeText(content);
      showNotification('success', STATUS_TOAST_MESSAGES.COPY_SUCCESS);
    } catch (error) {
      showNotification('error', STATUS_TOAST_MESSAGES.COPY_FAILED);
    }
  };

  const formatTimestamp = (timestamp: string) => {
    return new Date(timestamp).toLocaleTimeString('ru-RU', {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const MessageCard = ({ message }: { message: Message }) => {
    const isUser = message.role === 'user';
    
    return (
      <Box
        sx={{
          display: 'flex',
          justifyContent: isUser ? 'flex-end' : 'flex-start',
          mb: 2,
        }}
      >
        <Card
          sx={{
            maxWidth: '70%',
            minWidth: '200px',
            backgroundColor: isUser 
              ? 'primary.main' 
              : 'background.paper',
            color: isUser ? 'white' : 'text.primary',
            borderRadius: 3,
            position: 'relative',
            '&:hover .copy-button': {
              opacity: 1,
            },
          }}
        >
          <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
            {/* Заголовок сообщения */}
            <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
              <Avatar
                sx={{
                  width: 24,
                  height: 24,
                  mr: 1,
                  backgroundColor: isUser ? 'rgba(255,255,255,0.2)' : 'primary.main',
                }}
              >
                {isUser ? <PersonIcon fontSize="small" /> : <BotIcon fontSize="small" />}
              </Avatar>
              <Typography variant="caption" sx={{ opacity: 0.8 }}>
                {isUser ? 'Вы' : 'ГазикИИ'}
              </Typography>
              <Typography variant="caption" sx={{ opacity: 0.6, ml: 'auto' }}>
                {formatTimestamp(message.timestamp)}
              </Typography>
            </Box>

            {/* Содержимое сообщения */}
            {isUser ? (
              <Typography
                variant="body1"
                sx={{
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  lineHeight: 1.5,
                  fontSize: (() => {
                    const saved = localStorage.getItem('chat-font-size');
                    if (saved === 'small') return '0.875rem';
                    if (saved === 'large') return '1.125rem';
                    return '1rem';
                  })(),
                }}
              >
                {message.content}
              </Typography>
            ) : (
              <MessageRenderer 
                content={message.content} 
                isStreaming={message.isStreaming || false}
                onSendMessage={(prompt) => {
                  if (currentChat && (isConnected || isConnecting) && !state.isLoading) {
                    sendMessage(prompt, currentChat.id);
                  }
                }}
              />
            )}

            {/* Индикатор потоковой генерации */}
            {message.isStreaming && (
              <Box sx={{ mt: 1, display: 'flex', alignItems: 'center', gap: 1 }}>
                <Box
                  sx={{
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    backgroundColor: 'secondary.main',
                    animation: 'pulse 1.5s infinite',
                    '@keyframes pulse': {
                      '0%': { opacity: 1, transform: 'scale(1)' },
                      '50%': { opacity: 0.5, transform: 'scale(1.2)' },
                      '100%': { opacity: 1, transform: 'scale(1)' },
                    },
                  }}
                />
                <Chip
                  size="small"
                  label="Генерирую..."
                  color="secondary"
                  variant="outlined"
                  sx={{ 
                    fontSize: '0.75rem',
                    '& .MuiChip-label': { px: 1 }
                  }}
                />
              </Box>
            )}

            {/* Кнопка копирования */}
            <IconButton
              className="copy-button"
              size="small"
              onClick={() => handleCopyMessage(message.content)}
              sx={{
                position: 'absolute',
                top: 8,
                right: 8,
                opacity: 0,
                transition: 'opacity 0.2s ease',
                backgroundColor: 'rgba(0,0,0,0.1)',
                color: isUser ? 'white' : 'text.secondary',
                '&:hover': {
                  backgroundColor: 'rgba(0,0,0,0.2)',
                },
              }}
            >
              <CopyIcon fontSize="small" />
            </IconButton>
          </CardContent>
        </Card>
      </Box>
    );
  };

  return (
    <Box sx={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* Заголовок */}
      <Paper
        elevation={2}
        sx={{
          p: 2,
          borderRadius: 0,
          borderBottom: '1px solid',
          borderColor: 'divider',
        }}
      >
        <Container maxWidth="lg">
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <Box>
              <Typography variant="h5" component="h1" gutterBottom>
                Текстовый чат
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Общайтесь с вашим ИИ-ассистентом
              </Typography>
            </Box>
            
            {/* Статус соединения и управление */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              
              {messages.length > 0 && (
                <Tooltip title="Очистить историю">
                  <IconButton
                    size="small"
                    onClick={() => currentChat && clearMessages(currentChat.id)}
                    color="secondary"
                    sx={{ 
                      backgroundColor: 'action.hover',
                      '&:hover': {
                        backgroundColor: 'action.selected',
                      }
                    }}
                  >
                    <ClearIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              )}
              
              <Chip
                label={isConnected ? 'Подключено' : 'Отключено'}
                color={isConnected ? 'success' : 'error'}
                size="small"
                sx={{
                  '&.MuiChip-colorSuccess': {
                    backgroundColor: 'success.main',
                    color: 'white',
                  },
                  '&.MuiChip-colorError': {
                    backgroundColor: 'error.main',
                    color: 'white',
                  },
                }}
              />
              
              {/* Дополнительная информация о соединении */}
              {!isConnected && !isConnecting && (
                <Typography variant="caption" color="error.main" sx={{ ml: 1 }}>
                  Переподключение...
                </Typography>
              )}
              
              {/* Кнопка переподключения */}
              {!isConnected && !isConnecting && (
                <Tooltip title="Принудительно переподключиться">
                  <IconButton
                    size="small"
                    onClick={() => {
                      reconnect();
                      showNotification('info', 'Переподключение...');
                    }}
                    color="primary"
                    sx={{ 
                      backgroundColor: 'primary.main',
                      color: 'white',
                      '&:hover': {
                        backgroundColor: 'primary.dark',
                      }
                    }}
                  >
                    <RefreshIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              )}
            </Box>
          </Box>
        </Container>
      </Paper>

      {/* Область сообщений */}
      <Box sx={{ flexGrow: 1, overflow: 'hidden', position: 'relative' }}>
        {/* Индикатор загрузки */}
        {state.isLoading && (
          <LinearProgress
            sx={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              zIndex: 1000,
            }}
          />
        )}

        <Container
          maxWidth="lg"
          sx={{
            height: '100%',
            py: 2,
            overflow: 'auto',
            scrollBehavior: 'smooth',
          }}
        >
          {messages.length === 0 ? (
            /* Приветственное сообщение */
            <Box
              sx={{
                height: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                textAlign: 'center',
              }}
            >
              <Card sx={{ p: 4, maxWidth: 500 }}>
                <Avatar
                  sx={{
                    width: 80,
                    height: 80,
                    mx: 'auto',
                    mb: 2,
                    backgroundColor: 'primary.main',
                  }}
                >
                  <BotIcon sx={{ fontSize: 40 }} />
                </Avatar>
                <Typography variant="h5" fontWeight="600" gutterBottom>
                  Добро пожаловать в AstraChat! 👋
                </Typography>
                <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
                  Я ваш персональный ассистент - AstraChat. Задавайте любые вопросы,
                  и я постараюсь помочь вам найти ответы.
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Начните диалог, введя сообщение в поле ниже 👇
                </Typography>
              </Card>
            </Box>
          ) : (
            /* Список сообщений */
            <Box>
              {messages.map((message) => (
                <MessageCard key={message.id} message={message} />
              ))}
              <div ref={messagesEndRef} />
            </Box>
          )}
        </Container>
      </Box>

      {/* Поле ввода */}
      <Paper
        elevation={8}
        sx={{
          p: 2,
          borderRadius: 0,
          borderTop: '1px solid',
          borderColor: 'divider',
        }}
      >
        <Container maxWidth="lg">
          {/* Поле ввода и кнопки */}
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-end' }}>
            <TextField
              ref={inputRef}
              fullWidth
              multiline
              maxRows={4}
              value={inputMessage}
              onChange={(e) => setInputMessage(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="Введите сообщение..."
              disabled={(!isConnected && !isConnecting) || state.isLoading}
              sx={{
                '& .MuiOutlinedInput-root': {
                  borderRadius: 3,
                },
              }}
            />
            <Box sx={{ display: 'flex', gap: 1 }}>
              {state.isLoading && (
                <Tooltip title="Остановить генерацию">
                  <IconButton
                    onClick={() => {
                      // TODO: Добавить логику остановки генерации
                      showNotification('info', 'Функция остановки в разработке');
                    }}
                    color="warning"
                    sx={{ 
                      backgroundColor: 'warning.main',
                      color: 'white',
                      '&:hover': {
                        backgroundColor: 'warning.dark',
                      }
                    }}
                  >
                    <StopIcon />
                  </IconButton>
                </Tooltip>
              )}
                              <Tooltip title={(!inputMessage.trim() || (!isConnected && !isConnecting) || state.isLoading) ? 'Недоступно' : 'Отправить сообщение'}>
                  <span>
                    <IconButton
                      onClick={handleSendMessage}
                      disabled={!inputMessage.trim() || (!isConnected && !isConnecting) || state.isLoading}
                      color="primary"
                      sx={{
                        backgroundColor: 'primary.main',
                        color: 'white',
                        '&:hover': {
                          backgroundColor: 'primary.dark',
                        },
                        '&:disabled': {
                          backgroundColor: 'action.disabledBackground',
                          color: 'action.disabled',
                        },
                      }}
                    >
                      <SendIcon />
                    </IconButton>
                  </span>
                </Tooltip>
            </Box>
          </Box>
          
          {/* Подсказки */}
          <Box sx={{ mt: 1, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Typography variant="caption" color="text.secondary">
              Enter - отправить, Shift+Enter - новая строка
            </Typography>
            {inputMessage.length > 0 && (
              <Typography variant="caption" color="text.secondary">
                {inputMessage.length} символов
              </Typography>
            )}
          </Box>
        </Container>
      </Paper>

    </Box>
  );
}
