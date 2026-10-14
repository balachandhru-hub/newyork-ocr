export interface LoginRequestDto {
  userName: string;
  password: string;
}

export interface LoginResponseDto {
  status: number;
  message: string;
  personId: number;
  name: string;
  email: string;
  role: string;
}
