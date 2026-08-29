from pydantic import BaseModel
from typing import Optional


class ExperienceItem(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    isCurrent: bool = False
    description: Optional[str] = None


class EducationItem(BaseModel):
    school: Optional[str] = None
    degree: Optional[str] = None
    fieldOfStudy: Optional[str] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    grade: Optional[str] = None
    description: Optional[str] = None


class CertificationItem(BaseModel):
    name: Optional[str] = None
    authority: Optional[str] = None
    issuedDate: Optional[str] = None
    expiryDate: Optional[str] = None
    licenseNumber: Optional[str] = None
    url: Optional[str] = None


class SkillItem(BaseModel):
    name: str
    endorsements: int = 0


class LanguageItem(BaseModel):
    name: str
    proficiency: Optional[str] = None


class ProfileResponse(BaseModel):
    publicIdentifier: str
    name: Optional[str] = None
    headline: Optional[str] = None
    location: Optional[str] = None
    about: Optional[str] = None
    connections: Optional[int] = None
    followers: Optional[int] = None
    profilePicture: Optional[str] = None
    backgroundImage: Optional[str] = None
    experience: list[ExperienceItem] = []
    education: list[EducationItem] = []
    certifications: list[CertificationItem] = []
    skills: list[SkillItem] = []
    languages: list[LanguageItem] = []
