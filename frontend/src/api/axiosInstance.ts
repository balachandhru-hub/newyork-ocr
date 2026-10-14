import axios from 'axios';

const instance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 500000,
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true,
});

let isRefreshing = false;
let failedQueue: Array<{ resolve: (value?: unknown) => void; reject: (reason?: any) => void }> = [];

const processQueue = (error: any = null) => {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error);
    } else {
      prom.resolve();
    }
  });
  failedQueue = [];
};

instance.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    // Do not retry refresh-token, login, or register endpoints to avoid infinite loops
    if (
      error.response?.status === 401 &&
      originalRequest &&
      !originalRequest._retry &&
      !originalRequest.url?.includes('/api/v1/refresh-token') &&
      !originalRequest.url?.includes('/api/v1/login') &&
      !originalRequest.url?.includes('/api/v1/register') &&
      !originalRequest.url?.includes('/api/v1/logout')
    ) {
      originalRequest._retry = true;

      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        })
          .then(() => instance(originalRequest))
          .catch((err) => Promise.reject(err));
      }

      isRefreshing = true;

      try {
        const baseURL = import.meta.env.VITE_API_BASE_URL || '';
        await axios.get(`${baseURL}/api/v1/refresh-token`, { withCredentials: true });
        processQueue(null);
        return instance(originalRequest);
      } catch (refreshError: any) {
        processQueue(refreshError);
        
        const detailMsg = refreshError?.response?.data?.detail || refreshError?.response?.data?.message || '';
        const isInvalidOrExpired =
          refreshError?.response?.status === 401 ||
          (typeof detailMsg === 'string' && (
            detailMsg.toLowerCase().includes('invalid or expired token') ||
            detailMsg.toLowerCase().includes('access token missing') ||
            detailMsg.toLowerCase().includes('token expired')
          ));

        if (isInvalidOrExpired) {
          if (typeof window !== 'undefined' && !window.location.pathname.includes('/login')) {
            window.location.href = '/login';
          }
        }
        return Promise.reject(refreshError);
      } finally {
        isRefreshing = false;
      }
    }

    return Promise.reject(error);
  }
);

export default instance;
