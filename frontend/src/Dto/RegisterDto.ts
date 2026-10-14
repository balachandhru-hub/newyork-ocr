export interface RegisterRequestDto {
  name: string;
  email: string;
  companyName: string;
  mobile: string;
  country: string;
  address: string;
  role: 'User' | 'Consultant' | 'Lead';
  userName: string;
  password: string;
}

export interface RegisterResponseDto {
  status: number;
  message: string;
  personId: number;
}
