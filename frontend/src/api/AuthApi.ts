import axios from './axiosInstance';
import type { LoginRequestDto, LoginResponseDto } from '../Dto/LoginDto';
import type { RegisterRequestDto, RegisterResponseDto } from '../Dto/RegisterDto';
import type { RefreshTokenResponseDto } from '../Dto/RefreshTokenDto';
import type { PersonDetailsResponseDto } from '../Dto/PersonDetailsDto';
import type { LogoutResponseDto } from '../Dto/LogoutDto';
import type { ErrorResponseDto } from '../Dto/ErrorResponseDto';

export const loginUser = async (
  data: LoginRequestDto
): Promise<LoginResponseDto | ErrorResponseDto> => {
  try {
    const response = await axios.post<LoginResponseDto>(
      `/api/v1/login`,
      data,
      { withCredentials: true }
    );
    return response.data;
  } catch (error: any) {
    if (error.response && error.response.data) {
      const errData = error.response.data;
      return {
        statusCode: error.response.status || 500,
        message: errData.detail || errData.message || 'Unknown error',
        description: errData.description || errData.detail || 'No details provided',
      };
    }

    return {
      statusCode: 500,
      message: 'Unexpected Error',
      description: error.message || 'Something went wrong while logging in.',
    };
  }
};

export const registerUser = async (
  data: RegisterRequestDto
): Promise<RegisterResponseDto | ErrorResponseDto> => {
  try {
    const response = await axios.post<RegisterResponseDto>(
      `/api/v1/register`,
      data,
      { withCredentials: true }
    );
    return response.data;
  } catch (error: any) {
    if (error.response && error.response.data) {
      const errData = error.response.data;
      const msg = Array.isArray(errData.detail)
        ? errData.detail.map((d: any) => d.msg).join(', ')
        : errData.detail || errData.message || 'Unknown error';

      return {
        statusCode: error.response.status || 500,
        message: msg,
        description: errData.description || 'No details provided',
      };
    }

    return {
      statusCode: 500,
      message: 'Unexpected Error',
      description: error.message || 'Something went wrong while registering user.',
    };
  }
};

export const refreshToken = async (): Promise<RefreshTokenResponseDto | ErrorResponseDto> => {
  try {
    const response = await axios.get<RefreshTokenResponseDto>(
      `/api/v1/refresh-token`,
      { withCredentials: true }
    );
    return response.data;
  } catch (error: any) {
    if (error.response && error.response.data) {
      const errData = error.response.data;
      const detailMsg = typeof errData.detail === 'string' ? errData.detail : (errData.message || '');

      if (
        error.response.status === 401 ||
        detailMsg.toLowerCase().includes('invalid or expired token') ||
        detailMsg.toLowerCase().includes('access token missing')
      ) {
        if (typeof window !== 'undefined' && !window.location.pathname.includes('/login')) {
          window.location.href = '/login';
        }
      }

      return {
        statusCode: error.response.status || 401,
        message: detailMsg || 'Token refresh failed',
        description: errData.description || 'Session expired',
      };
    }

    return {
      statusCode: 500,
      message: 'Unexpected Error',
      description: error.message || 'Something went wrong while refreshing token.',
    };
  }
};

export const logoutUser = async (): Promise<LogoutResponseDto | ErrorResponseDto> => {
  try {
    const response = await axios.post<LogoutResponseDto>(
      `/api/v1/logout`,
      {},
      { withCredentials: true }
    );
    return response.data;
  } catch (error: any) {
    if (error.response && error.response.data) {
      const errData = error.response.data;
      return {
        statusCode: error.response.status || 500,
        message: errData.detail || errData.message || 'Logout failed',
        description: errData.description || 'Failed to logout session',
      };
    }

    return {
      statusCode: 500,
      message: 'Unexpected Error',
      description: error.message || 'Something went wrong while logging out.',
    };
  }
};

export const getPersonDetails = async (): Promise<PersonDetailsResponseDto | ErrorResponseDto> => {
  try {
    const response = await axios.get<PersonDetailsResponseDto>(
      `/api/v1/person/details`,
      { withCredentials: true }
    );
    return response.data;
  } catch (error: any) {
    if (error.response && error.response.data) {
      const errData = error.response.data;
      return {
        statusCode: error.response.status || 500,
        message: errData.detail || errData.message || 'Failed to fetch person details',
        description: errData.description || 'No details provided',
      };
    }

    return {
      statusCode: 500,
      message: 'Unexpected Error',
      description: error.message || 'Something went wrong while fetching person details.',
    };
  }
};

export const forgotPassword = async (email: string) => {
  try {
    const response = await axios.post('/api/v1/forgot-password', { email });
    return response.data;
  } catch (error: any) {
    if (error.response && error.response.data) {
      return error.response.data;
    }
    return { error: true, message: 'Failed to request password reset' };
  }
};

export const resetPassword = async (payload: any) => {
  try {
    const response = await axios.post('/api/v1/reset-password', payload);
    return response.data;
  } catch (error: any) {
    if (error.response && error.response.data) {
      return error.response.data;
    }
    return { error: true, message: 'Failed to reset password' };
  }
};
