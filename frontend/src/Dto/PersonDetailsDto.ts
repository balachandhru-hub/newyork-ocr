export interface PersonData {
  id: number;
  name: string;
  email: string;
  companyName: string;
  mobile: string;
  country: string;
  address: string;
  role: string;
  userName: string;
  creditsRemaining: number;
  dateCreated: string;
  dateUpdated: string;
  isActive: boolean;
  createdBy: number;
  updatedBy: number;
}

export interface PersonDetailsResponseDto {
  status: number;
  data: PersonData;
}
