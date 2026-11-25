import React, { useState, useEffect } from 'react';
import {
  Box,
  Card,
  CardContent,
  TextField,
  Button,
  Typography,
  Alert,
  CircularProgress,
  Container,
  IconButton,
  InputAdornment,
  Divider,
} from '@mui/material';
import {
  RemoveRedEye as VisibilityIcon,
  VisibilityOff as VisibilityOffIcon,
  WbSunny as SunIcon,
  Brightness3 as MoonIcon,
  AdminPanelSettings as SsoIcon,
} from '@mui/icons-material';
import { useAuth, AuthLoginError } from '../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';
import {
  initSettings,
  formatRemainingAttemptsHint,
  LOGIN_LOCKOUT_EXHAUSTED_MESSAGE,
  type LoginLockoutConfig,
} from '../settings';
import { fetchLoginLockoutPolicy, getApiUrl, API_ENDPOINTS } from '../config/api';
import LoginSessionNotice from '../components/LoginSessionNotice';

type AuthMode = 'login' | 'register';

export default function LoginPage() {
  const [mode, setMode] = useState<AuthMode>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [attemptHint, setAttemptHint] = useState('');
  const [lockoutPolicy, setLockoutPolicy] = useState<LoginLockoutConfig | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [keycloakSsoEnabled, setKeycloakSsoEnabled] = useState(false);
  const [keycloakLoginUrl, setKeycloakLoginUrl] = useState('');
  const [isSsoRedirecting, setIsSsoRedirecting] = useState(false);
  const [registrationEnabled, setRegistrationEnabled] = useState(false);
  const [isDarkMode, setIsDarkMode] = useState(() => {
    const saved = localStorage.getItem('theme');
    return saved ? saved === 'dark' : false;
  });

  const { login, register } = useAuth();
  const navigate = useNavigate();
  const isRegisterMode = mode === 'register';

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await initSettings();
      } catch (error) {
        console.warn('Не удалось загрузить настройки frontend:', error);
      }

      try {
        const cfg = await fetchLoginLockoutPolicy();
        if (!cancelled) {
          setLockoutPolicy(cfg);
        }
      } catch (e) {
        console.warn(
          'Не удалось загрузить политику блокировки с backend (GET /api/auth/login-lockout-policy):',
          e,
        );
      }

      try {
        const regResp = await fetch(getApiUrl(API_ENDPOINTS.AUTH_REGISTRATION_POLICY));
        if (regResp.ok) {
          const regData = await regResp.json();
          if (!cancelled) {
            setRegistrationEnabled(!!regData?.registration_enabled);
            if (!regData?.registration_enabled) {
              setMode('login');
            }
          }
        }
      } catch (regError) {
        console.warn('Не удалось загрузить политику регистрации:', regError);
        if (!cancelled) {
          setRegistrationEnabled(false);
          setMode('login');
        }
      }

      try {
        const ssoResponse = await fetch(getApiUrl(API_ENDPOINTS.AUTH_SSO_PROVIDERS));
        if (!ssoResponse.ok) {
          console.warn(
            'SSO-провайдеры: backend вернул HTTP %s для GET /api/auth/sso/providers',
            ssoResponse.status,
          );
          return;
        }
        const ssoData = await ssoResponse.json();
        if (!cancelled && ssoData.keycloak_enabled) {
          setKeycloakSsoEnabled(true);
          setKeycloakLoginUrl(
            ssoData.keycloak_login_url || getApiUrl(API_ENDPOINTS.AUTH_SSO_KEYCLOAK_LOGIN),
          );
        }
      } catch (ssoError) {
        console.warn('Не удалось загрузить SSO-провайдеры:', ssoError);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const toggleTheme = () => {
    const newMode = !isDarkMode;
    setIsDarkMode(newMode);
    localStorage.setItem('theme', newMode ? 'dark' : 'light');
  };

  const togglePasswordVisibility = () => {
    setShowPassword(!showPassword);
  };

  const handleSsoLogin = () => {
    if (!keycloakLoginUrl || isLoading || isSsoRedirecting) return;
    setIsSsoRedirecting(true);
    window.location.href = keycloakLoginUrl;
  };

  const ssoButtonBg = isDarkMode ? '#2a2a2a' : '#f5f5f5';
  const ssoButtonHoverBg = isDarkMode ? '#333333' : '#ebebeb';
  const ssoButtonBorder = isDarkMode ? 'rgba(255,255,255,0.18)' : 'rgba(0,0,0,0.12)';
  const ssoButtonText = isDarkMode ? '#fff' : '#1a1a1a';

  const inputBackgroundColor = isDarkMode ? '#2a2a2a' : '#ffffff';
  const inputTextColor = isDarkMode ? '#fff' : '#000';

  /** Высота и скругление как у MuiOutlinedInput (TextField по умолчанию). */
  const controlHeight = 56;
  const controlBorderRadius = 1;
  const controlButtonSx = {
    height: controlHeight,
    minHeight: controlHeight,
    borderRadius: controlBorderRadius,
  };

  React.useEffect(() => {
    document.body.style.backgroundColor = isDarkMode ? '#121212' : '#f5f5f5';
    return () => {
      document.body.style.backgroundColor = '';
    };
  }, [isDarkMode]);

  const glassCardSx = {
    width: '100%',
    maxWidth: 450,
    borderRadius: '20px',
    backgroundColor: isDarkMode ? 'rgba(30, 30, 30, 0.45)' : 'rgba(255, 255, 255, 0.45)',
    backgroundImage: 'none',
    border: isDarkMode
      ? '1px solid rgba(255, 255, 255, 0.18)'
      : '1px solid rgba(255, 255, 255, 0.65)',
    boxShadow: isDarkMode
      ? '0 8px 32px rgba(0, 0, 0, 0.35)'
      : '0 8px 32px rgba(0, 0, 0, 0.1)',
    backdropFilter: 'blur(18px)',
    WebkitBackdropFilter: 'blur(18px)',
  } as const;

  const pageBackdropSx = {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    // Лёгкий градиент из палитры темы — чтобы glass-blur читался на фоне
    background: isDarkMode
      ? `
        radial-gradient(ellipse 80% 60% at 20% 20%, rgba(92, 107, 192, 0.22) 0%, transparent 55%),
        radial-gradient(ellipse 70% 50% at 80% 80%, rgba(255, 179, 0, 0.08) 0%, transparent 50%),
        linear-gradient(160deg, #121212 0%, #1a1a1a 45%, #16161c 100%)
      `
      : `
        radial-gradient(ellipse 80% 60% at 15% 15%, rgba(92, 107, 192, 0.16) 0%, transparent 55%),
        radial-gradient(ellipse 70% 50% at 85% 85%, rgba(25, 118, 210, 0.08) 0%, transparent 50%),
        linear-gradient(160deg, #f5f5f5 0%, #eef0f5 50%, #f7f7f9 100%)
      `,
  } as const;

  const switchMode = (next: AuthMode) => {
    setMode(next);
    setError('');
    setAttemptHint('');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setAttemptHint('');
    setIsLoading(true);

    try {
      if (isRegisterMode) {
        await register(username, password);
      } else {
        await login(username, password);
      }
      navigate('/');
    } catch (err: unknown) {
      if (isRegisterMode) {
        const message =
          err instanceof Error ? err.message : 'Ошибка при регистрации';
        setError(message);
        setAttemptHint('');
      } else if (err instanceof AuthLoginError && err.meta.retryAfterSeconds !== undefined) {
        setError(LOGIN_LOCKOUT_EXHAUSTED_MESSAGE);
        setAttemptHint('');
      } else if (err instanceof AuthLoginError) {
        const message =
          err.message === 'Сервис аутентификации временно недоступен'
            ? err.message
            : 'Неверное имя пользователя или пароль';
        setError(message);

        const { remainingAttempts, maxFailedAttempts, lockoutDurationSeconds } = err.meta;
        const max = maxFailedAttempts ?? lockoutPolicy?.maxFailedAttempts;
        const lockSec =
          lockoutDurationSeconds ?? lockoutPolicy?.lockoutDurationSeconds ?? 900;
        if (remainingAttempts !== undefined && max !== undefined) {
          setAttemptHint(formatRemainingAttemptsHint(remainingAttempts, max, lockSec));
        } else {
          setAttemptHint('');
        }
      } else {
        const message =
          err instanceof Error ? err.message : 'Ошибка при входе в систему';
        setError(message);
        setAttemptHint('');
      }
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Box sx={pageBackdropSx}>
      <LoginSessionNotice />
      <IconButton
        onClick={toggleTheme}
        disableRipple
        sx={{
          position: 'fixed',
          top: 16,
          right: 16,
          zIndex: 1000,
          '&:hover': {
            backgroundColor: 'transparent',
            transform: 'scale(1.1)',
            transition: 'transform 0.2s ease-in-out',
          },
        }}
      >
        {isDarkMode ? (
          <SunIcon sx={{ color: '#ffb300', fontSize: 28 }} />
        ) : (
          <MoonIcon sx={{ color: '#5c6bc0', fontSize: 28 }} />
        )}
      </IconButton>

      <Container maxWidth="sm" sx={{ display: 'flex', justifyContent: 'center' }}>
        <Card elevation={0} sx={glassCardSx}>
          <CardContent sx={{ p: 4 }}>
            <Box
              sx={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                mb: 3,
              }}
            >
              <Box
                component="img"
                src="/astra.png"
                alt="Astra Logo"
                sx={{
                  width: 100,
                  height: 100,
                  mb: 2,
                  objectFit: 'contain',
                }}
              />
              <Typography
                variant="h4"
                component="h1"
                fontWeight="bold"
                sx={{ color: isDarkMode ? '#fff' : '#000' }}
              >
                {isRegisterMode ? 'Регистрация' : 'Вход в систему'}
              </Typography>
              <Typography
                variant="body2"
                sx={{
                  mt: 1,
                  color: isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)',
                  textAlign: 'center',
                }}
              >
                {isRegisterMode
                  ? 'Укажите имя пользователя и пароль'
                  : 'Используйте ваши данные учётной записи'}
              </Typography>
            </Box>

            {(error || attemptHint) && (
              <Alert severity="error" sx={{ mb: 3 }}>
                {error}
                {error && attemptHint ? (
                  <Typography component="p" variant="body2" sx={{ mt: 1, mb: 0 }}>
                    {attemptHint}
                  </Typography>
                ) : (
                  attemptHint
                )}
              </Alert>
            )}

            <form onSubmit={handleSubmit}>
              <TextField
                fullWidth
                label="Имя пользователя"
                variant="outlined"
                margin="normal"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={isLoading}
                required
                autoFocus
                autoComplete="username"
                sx={{
                  '& .MuiOutlinedInput-root': {
                    backgroundColor: inputBackgroundColor,
                    colorScheme: isDarkMode ? 'dark' : 'light',
                    '& fieldset': {
                      borderColor: isDarkMode ? 'rgba(255,255,255,0.23)' : 'rgba(0,0,0,0.23)',
                    },
                    '&:hover fieldset': {
                      borderColor: isDarkMode ? 'rgba(255,255,255,0.4)' : 'rgba(0,0,0,0.4)',
                    },
                    '&.Mui-focused fieldset': {
                      borderColor: 'primary.main',
                      borderWidth: '2px',
                    },
                  },
                  '& .MuiInputLabel-root': {
                    color: isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)',
                    '&.Mui-focused': {
                      color: 'primary.main',
                    },
                  },
                  '& .MuiInputBase-input': {
                    color: inputTextColor,
                    colorScheme: isDarkMode ? 'dark' : 'light',
                  },
                  '& input:-webkit-autofill, & input:-webkit-autofill:hover, & input:-webkit-autofill:focus, & input:-webkit-autofill:active, & .MuiOutlinedInput-input:-webkit-autofill, & .MuiOutlinedInput-input:-webkit-autofill:hover, & .MuiOutlinedInput-input:-webkit-autofill:focus, & .MuiOutlinedInput-input:-webkit-autofill:active': {
                    WebkitBoxShadow: `0 0 0 1000px ${inputBackgroundColor} inset !important`,
                    boxShadow: `0 0 0 1000px ${inputBackgroundColor} inset !important`,
                    WebkitTextFillColor: `${inputTextColor} !important`,
                    color: `${inputTextColor} !important`,
                    caretColor: `${inputTextColor} !important`,
                    backgroundColor: `${inputBackgroundColor} !important`,
                    WebkitBackgroundClip: 'padding-box !important',
                    transition: 'background-color 999999s ease-in-out 0s',
                  },
                }}
              />

              <TextField
                fullWidth
                label="Пароль"
                type={showPassword ? 'text' : 'password'}
                variant="outlined"
                margin="normal"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={isLoading}
                required
                autoComplete={isRegisterMode ? 'new-password' : 'current-password'}
                sx={{
                  '& .MuiOutlinedInput-root': {
                    backgroundColor: inputBackgroundColor,
                    colorScheme: isDarkMode ? 'dark' : 'light',
                    '& fieldset': {
                      borderColor: isDarkMode ? 'rgba(255,255,255,0.23)' : 'rgba(0,0,0,0.23)',
                    },
                    '&:hover fieldset': {
                      borderColor: isDarkMode ? 'rgba(255,255,255,0.4)' : 'rgba(0,0,0,0.4)',
                    },
                    '&.Mui-focused fieldset': {
                      borderColor: 'primary.main',
                      borderWidth: '2px',
                    },
                  },
                  '& .MuiInputLabel-root': {
                    color: isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)',
                    '&.Mui-focused': {
                      color: 'primary.main',
                    },
                  },
                  '& .MuiInputBase-input': {
                    color: inputTextColor,
                    colorScheme: isDarkMode ? 'dark' : 'light',
                  },
                  '& input:-webkit-autofill, & input:-webkit-autofill:hover, & input:-webkit-autofill:focus, & input:-webkit-autofill:active, & .MuiOutlinedInput-input:-webkit-autofill, & .MuiOutlinedInput-input:-webkit-autofill:hover, & .MuiOutlinedInput-input:-webkit-autofill:focus, & .MuiOutlinedInput-input:-webkit-autofill:active': {
                    WebkitBoxShadow: `0 0 0 1000px ${inputBackgroundColor} inset !important`,
                    boxShadow: `0 0 0 1000px ${inputBackgroundColor} inset !important`,
                    WebkitTextFillColor: `${inputTextColor} !important`,
                    color: `${inputTextColor} !important`,
                    caretColor: `${inputTextColor} !important`,
                    backgroundColor: `${inputBackgroundColor} !important`,
                    WebkitBackgroundClip: 'padding-box !important',
                    transition: 'background-color 999999s ease-in-out 0s',
                  },
                }}
                InputProps={{
                  endAdornment: (
                    <InputAdornment position="end">
                      <IconButton
                        onClick={togglePasswordVisibility}
                        edge="end"
                        disabled={isLoading}
                        disableRipple
                        sx={{
                          color: isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)',
                          backgroundColor: 'transparent',
                          '&:hover': {
                            backgroundColor: 'transparent',
                            color: isDarkMode ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.87)',
                            transform: 'scale(1.1)',
                            transition: 'all 0.2s ease-in-out',
                          },
                          '&:active': {
                            transform: 'scale(0.95)',
                          },
                          '& .MuiSvgIcon-root': {
                            fontSize: '22px',
                          },
                        }}
                      >
                        {showPassword ? <VisibilityIcon /> : <VisibilityOffIcon />}
                      </IconButton>
                    </InputAdornment>
                  ),
                }}
              />

              <Button
                fullWidth
                type="submit"
                variant="contained"
                size="large"
                disabled={isLoading || isSsoRedirecting}
                sx={{
                  mt: 3,
                  mb: 0,
                  ...controlButtonSx,
                  bgcolor: 'primary.main',
                  '&:hover': {
                    bgcolor: 'primary.dark',
                  },
                }}
              >
                {isLoading ? (
                  <CircularProgress size={24} color="inherit" />
                ) : isRegisterMode ? (
                  'Зарегистрироваться'
                ) : (
                  'Войти'
                )}
              </Button>

              {registrationEnabled || isRegisterMode ? (
                <Button
                  fullWidth
                  type="button"
                  variant="text"
                  disabled={isLoading || isSsoRedirecting || !registrationEnabled}
                  onClick={() => switchMode(isRegisterMode ? 'login' : 'register')}
                  sx={{
                    mt: 1.5,
                    textTransform: 'none',
                    color: isDarkMode ? 'rgba(255,255,255,0.75)' : 'rgba(0,0,0,0.6)',
                    '&:hover': {
                      backgroundColor: 'transparent',
                      color: isDarkMode ? '#fff' : '#000',
                    },
                  }}
                >
                  {isRegisterMode
                    ? 'Уже есть аккаунт? Войти'
                    : 'Нет аккаунта? Регистрация'}
                </Button>
              ) : null}
            </form>

            {keycloakSsoEnabled && !isRegisterMode && (
              <>
                <Divider sx={{ my: 2.5 }}>
                  <Typography
                    variant="body2"
                    sx={{ color: isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.45)' }}
                  >
                    или
                  </Typography>
                </Divider>
                <Button
                  fullWidth
                  type="button"
                  variant="outlined"
                  size="large"
                  disabled={isLoading || isSsoRedirecting}
                  onClick={handleSsoLogin}
                  aria-label="Войти через SSO"
                  startIcon={
                    isSsoRedirecting ? (
                      <CircularProgress size={20} color="inherit" />
                    ) : (
                      <SsoIcon sx={{ fontSize: 22 }} />
                    )
                  }
                  sx={{
                    ...controlButtonSx,
                    textTransform: 'none',
                    fontSize: '1rem',
                    fontWeight: 500,
                    color: ssoButtonText,
                    bgcolor: ssoButtonBg,
                    borderColor: ssoButtonBorder,
                    justifyContent: 'center',
                    gap: 1,
                    '&:hover': {
                      bgcolor: ssoButtonHoverBg,
                      borderColor: isDarkMode ? 'rgba(255,255,255,0.28)' : 'rgba(0,0,0,0.2)',
                    },
                  }}
                >
                  {isSsoRedirecting ? 'Переход к SSO…' : 'Войти через SSO'}
                </Button>
              </>
            )}
          </CardContent>
        </Card>
      </Container>
    </Box>
  );
}
